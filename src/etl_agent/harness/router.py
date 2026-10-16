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

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.actor_resolver import ActorContext
from etl_agent.auth.dependencies import require_actor_for_project, require_project_member
from etl_agent.auth.errors import ValidationError
from etl_agent.config import Settings
from etl_agent.harness import service as harness_service
from etl_agent.harness.approve.service import ApproveService
from etl_agent.harness.commit.service import CommitService
from etl_agent.harness.prepare.service import PrepareInput, PrepareService
from etl_agent.harness.schemas import (
    ApprovalSlotView,
    CommitRequest,
    CommitResponse,
    DecisionRequest,
    DecisionResponse,
    PreparationCreateRequest,
    PreparationListView,
    PreparationResponse,
)
from etl_agent.infrastructure.database import get_session
from etl_agent.infrastructure.models import ExecutionPreparation, HarnessApprovalDecision
from etl_agent.worker.capability_handoff import CapabilityHandoff, RedisCapabilityHandoff

router = APIRouter(prefix="/api/v1", tags=["harness"])

# 准备单合法状态（与 execution_preparations.status 状态机一致）
ALLOWED_PREPARATION_STATUSES: frozenset[str] = frozenset(
    {"DENIED", "WAITING_APPROVAL", "READY", "REJECTED", "EXPIRED", "COMMITTED"}
)


def _preparation_list_view(
    preparation: ExecutionPreparation,
    outcome: str,
    approvals: list[HarnessApprovalDecision],
) -> PreparationListView:
    """准备单行 → 列表项视图（展示口径；审批槽位按 role 稳定序输出）。"""
    return PreparationListView(
        id=preparation.id,
        pipeline_version_id=preparation.pipeline_version_id,
        decision=outcome,
        risk_level=preparation.risk_level,
        status=preparation.status,
        required_roles=list(preparation.required_roles or []),
        required_approvals=preparation.required_approvals or 0,
        approvals=[
            ApprovalSlotView(
                role=decision.role,
                decided_by=decision.decided_by,
                decision=decision.decision,
                reason=decision.reason,
                evidence_sha256=decision.evidence_sha256,
                decided_at=decision.created_at,
            )
            for decision in approvals
        ],
        impact_report=preparation.impact_report,
        rollback_plan=_rollback_plan_text(preparation.rollback_plan),
        expires_at=preparation.expires_at,
        created_at=preparation.created_at,
    )


def _rollback_plan_text(rollback_plan: dict[str, object] | None) -> str | None:
    """回滚计划是 JSON 事实；前端只需摘要文本——取 `summary` 或首个字符串值。"""
    if not rollback_plan:
        return None
    summary = rollback_plan.get("summary")
    if isinstance(summary, str):
        return summary
    for value in rollback_plan.values():
        if isinstance(value, str):
            return value
    return None


def _prepare_service(request: Request) -> PrepareService:
    """从应用状态取 Prepare 服务（生命周期内构建，含验签后的 PDP）。"""
    return cast("PrepareService", request.app.state.prepare_service)


def _approve_service() -> ApproveService:
    return ApproveService()


def _commit_service(request: Request) -> CommitService:
    return cast("CommitService", request.app.state.commit_service)


def _handoff(request: Request) -> CapabilityHandoff:
    """取 Capability 交接通道（应用生命周期装配；测试应用按需现建）。

    S7 起 Commit 必须把令牌本体交付到 Worker 通道：库中只存 `token_sha256`（FR-33），
    令牌本体若只出现在 Commit 返回值里，Worker 永远拿不到——那是「授权了但执行不了」
    的死路。通道写入失败即拒绝（`DependencyUnavailable`），**不降级为无令牌执行**
    （那等于绕过 Replay Guard，FR-34 红线）。
    """
    existing = getattr(request.app.state, "capability_handoff", None)
    if existing is not None:
        return cast("CapabilityHandoff", existing)
    settings = cast("Settings", request.app.state.settings)
    return RedisCapabilityHandoff(
        request.app.state.redis, key_prefix=settings.capability_handoff_key_prefix
    )


@router.get(
    "/projects/{project_id}/preparations",
    response_model=list[PreparationListView],
)
async def list_preparations(
    project_id: uuid.UUID,
    response: Response,
    status: str | None = Query(default=None, description="可选：按准备单状态过滤"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> list[PreparationListView]:
    """API-08 列表：项目下准备单（项目成员可读，审批台/运行中心）。

    准备单自带 `project_id`（`execution_preparations.project_id`），直接按项目隔离
    （FR-66 / AC-12）。每项带 PDP 判决与审批槽位事实，供前端渲染「待四眼审批」卡。

    分页：`limit`（1..200，默认 50）+ `offset`；同过滤条件下的**总数**经
    `X-Total-Count` 响应头回传——列表体保持裸数组（与前端既有消费方式一致）。

    Raises:
        UnknownActor:        未携带身份声明（401 AUTH_401_UNKNOWN_ACTOR）。
        NotProjectMember:    非成员或项目不存在（403，统一码不泄露存在性）。
        ValidationError:     status 非法（BIZ_400_VALIDATION）。
    """
    if status is not None and status not in ALLOWED_PREPARATION_STATUSES:
        raise ValidationError(
            f"status 取值非法：{status!r}，可选 {list(ALLOWED_PREPARATION_STATUSES)}"
        )
    rows, total = await harness_service.list_preparations_for_project(
        session, project_id, status=status, limit=limit, offset=offset
    )
    response.headers["X-Total-Count"] = str(total)
    return [
        _preparation_list_view(preparation, outcome, approvals)
        for preparation, outcome, approvals in rows
    ]


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
    result, token = await _commit_service(request).commit(
        session,
        preparation_id=preparation_id,
        project_id=preparation,
        operator=actor,
    )
    await session.commit()
    # 令牌本体交付 Worker 通道（Redis 单次读，TTL 与令牌过期对齐，FR-33）。
    # 必须在业务事务提交之后：先提交「已授权」事实，再交付令牌——顺序颠倒会出现
    # 「Worker 拿到令牌但库里还没有准备单 COMMITTED」的窗口。
    settings = cast("Settings", request.app.state.settings)
    await _handoff(request).publish(
        result.token_id, token, settings.capability_handoff_ttl_seconds
    )
    return CommitResponse(
        token_id=result.token_id,
        preparation_id=result.preparation_id,
        claims=result.claims,
        expires_at=result.expires_at,
        run_id=result.run_id,
        outbox_id=result.outbox_id,
    )
