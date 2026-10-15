"""M-J Harness 安全执行内核 — API 路由（API-08 创建准备单 / API-09 审批 / API-10 Commit）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-08/09/10 / 第 6.2 章接口契约 / 第 8 章「安全 · 多租户」
覆盖 FR：FR-31（准备单）、FR-32（四眼审批）、FR-33（Commit）、FR-66（项目隔离）
对应 AC：AC-01（四眼职责分离）、AC-05（prepare→approve→commit + 单次令牌）

鉴权：API-08 有 `project_id` 路径参数；API-09/10 经准备单回溯项目后校验成员。
PDP / 审批槽位 / 重验全部在服务层完成，路由只做编排（不在此处做安全判决）。
"""
from __future__ import annotations

import uuid
from typing import cast

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.actor_resolver import ActorContext
from etl_agent.auth.dependencies import require_actor_for_project, require_project_member
from etl_agent.harness import service as harness_service
from etl_agent.harness.approve.service import ApproveService
from etl_agent.harness.commit.service import CommitService
from etl_agent.harness.prepare.service import PrepareInput, PrepareService
from etl_agent.harness.schemas import (
    CommitRequest,
    CommitResponse,
    DecisionRequest,
    DecisionResponse,
    PreparationCreateRequest,
    PreparationResponse,
)
from etl_agent.infrastructure.database import get_session

router = APIRouter(prefix="/api/v1", tags=["harness"])


def _prepare_service(request: Request) -> PrepareService:
    """从应用状态取 Prepare 服务（生命周期内构建，含验签后的 PDP）。"""
    return cast("PrepareService", request.app.state.prepare_service)


def _approve_service() -> ApproveService:
    return ApproveService()


def _commit_service(request: Request) -> CommitService:
    return cast("CommitService", request.app.state.commit_service)


@router.post(
    "/projects/{project_id}/preparations",
    response_model=PreparationResponse,
    status_code=201,
)
async def create_preparation(
    project_id: uuid.UUID,
    payload: PreparationCreateRequest,
    request: Request,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> PreparationResponse:
    """创建准备单（API-08）：PDP 评级 + 指纹 / 影响报告 / 回滚计划 + 审批槽位。

    PDP DENY 返回 403 且**不落准备单**（FR-29 无放行通道）；同幂等键同载荷返回同一准备单。
    """
    preparation, decision = await _prepare_service(request).create_preparation(
        session,
        project_id=project_id,
        payload=PrepareInput(
            pipeline_version_id=payload.pipeline_version_id,
            idempotency_key=payload.idempotency_key,
            environment=payload.environment,
            tool_name=payload.tool_name,
            tool_version=payload.tool_version,
            actor_project_id=project_id,
            budget=payload.budget,
            target_schema_aligned=payload.target_schema_aligned,
            classifications=tuple(payload.classifications),
            side_effect_type=payload.side_effect_type,
            resource_ids=tuple(payload.resource_ids),
        ),
        requested_by=actor.user_id,
    )
    await session.commit()
    return PreparationResponse(
        id=preparation.id,
        status=preparation.status,
        risk_level=preparation.risk_level,
        artifact_sha256=preparation.artifact_sha256,
        input_fingerprint=preparation.input_fingerprint,
        resource_fingerprint=preparation.resource_fingerprint,
        required_roles=list(preparation.required_roles or []),
        required_approvals=preparation.required_approvals or 0,
        environment=preparation.environment,
        tool_name=preparation.tool_name,
        tool_version=preparation.tool_version,
        expires_at=preparation.expires_at,
        outcome=decision.outcome,
    )


@router.post("/preparations/{preparation_id}/decisions", response_model=DecisionResponse)
async def decide(
    preparation_id: uuid.UUID,
    payload: DecisionRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> DecisionResponse:
    """四眼审批决策（API-09）：自批 / 槽被占 / 职责缺失均拒绝（AC-01）。"""
    preparation = await harness_service.load_preparation_project(
        session, preparation_id
    )
    actor = await require_actor_for_project(request, session, preparation)
    outcome = await _approve_service().decide(
        session,
        preparation_id=preparation_id,
        project_id=preparation,
        actor=actor,
        role=payload.role,
        decision=payload.decision,
        reason=payload.reason,
        evidence_sha256=payload.evidence_sha256,
    )
    await session.commit()
    return DecisionResponse(
        preparation_id=outcome.preparation_id,
        role=outcome.role,
        decision=outcome.decision,
        decided_by=outcome.decided_by,
        status=outcome.status,
        approvals_received=outcome.approvals_received,
        approvals_required=outcome.approvals_required,
    )


@router.post("/preparations/{preparation_id}/commit", response_model=CommitResponse)
async def commit(
    preparation_id: uuid.UUID,
    request: Request,
    payload: CommitRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> CommitResponse:
    """Commit（API-10）：服务端重验 + 签发单次 Capability + 同事务 ExecutionRun/Outbox。

    仅返回 `token_id` 与 claims；令牌本体转交 Worker 通道，不在 API 响应中出现（FR-33）。
    """
    preparation = await harness_service.load_preparation_project(
        session, preparation_id
    )
    actor = await require_actor_for_project(request, session, preparation)
    result, _token = await _commit_service(request).commit(
        session,
        preparation_id=preparation_id,
        project_id=preparation,
        operator=actor,
    )
    await session.commit()
    return CommitResponse(
        token_id=result.token_id,
        preparation_id=result.preparation_id,
        claims=result.claims,
        expires_at=result.expires_at,
        run_id=result.run_id,
        outbox_id=result.outbox_id,
    )
