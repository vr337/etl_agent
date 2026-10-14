"""M-F 生成引擎 — API 路由（API-05 / API-06 / API-07）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-05/06/07 / 第 6.2 章接口契约 / 第 8 章「安全 · 多租户」
覆盖 FR：FR-16（创建运行）、FR-17（澄清恢复）、FR-26（冻结 SHA256）、FR-66（项目隔离）
对应 AC：AC-03（中断与恢复）、AC-04（门禁 + 冻结）、AC-12（跨项目 403）

鉴权口径：API-05 有 `project_id` 路径参数，走 `require_project_member` 依赖；
API-06/07 路径中无 `project_id`，先由资源回溯项目再做成员校验（`_require_membership`），
两者对「非成员」统一返回 403，不泄露资源是否存在（AC-12）。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.actor_resolver import ActorContext, resolve_actor
from etl_agent.auth.dependencies import require_project_member
from etl_agent.auth.errors import NotProjectMember, StateConflict, UnknownActor
from etl_agent.generation import artifacts as artifact_service
from etl_agent.generation import service as generation_service
from etl_agent.generation.schemas import (
    AgentRunCreateRequest,
    AgentRunMessageRequest,
    AgentRunResponse,
    PipelineVersionFreezeResponse,
)
from etl_agent.infrastructure.database import get_session
from etl_agent.infrastructure.models import AgentRun

router = APIRouter(prefix="/api/v1", tags=["generation"])


def _to_response(run: AgentRun) -> AgentRunResponse:
    """运行响应：只回状态 / 挂起问题 / 6 个非敏感遥测字段（FR-67）。"""
    return AgentRunResponse(
        id=run.id,
        thread_id=run.thread_id,
        status=run.status,
        pending_question=run.pending_question,
        generation_backend=run.generation_backend,
        generation_model=run.generation_model,
        generation_candidate_count=run.generation_candidate_count,
        generation_repair_count=run.generation_repair_count,
        generation_duration_ms=run.generation_duration_ms,
    )


async def _require_membership(
    request: Request, session: AsyncSession, project_id: uuid.UUID
) -> ActorContext:
    """资源回溯项目的成员校验（API-06/07 无 project_id 路径参数时的等价依赖）。"""
    subject = getattr(request.state, "actor_subject", None)
    if not subject:
        raise UnknownActor("请求未携带身份声明")
    actor = await resolve_actor(session, subject, project_id)
    if actor.member_role is None:
        raise NotProjectMember()
    return actor


@router.post(
    "/projects/{project_id}/agent-runs",
    status_code=201,
    response_model=AgentRunResponse,
)
async def create_agent_run(
    project_id: uuid.UUID,
    payload: AgentRunCreateRequest,
    request: Request,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> AgentRunResponse:
    """创建生成运行（API-05）：推进状态机直到中断 / 完成（FR-16/17）。

    返回推进后的真实状态（参数缺失 → `WAITING_INPUT`，门禁通过 → `WAITING_APPROVAL`），
    而非固定的 `QUEUED`——AC-03 要求创建调用即可观察到澄清挂起。
    """
    settings = request.app.state.settings
    run = await generation_service.create_run(
        session,
        project_id=project_id,
        payload=payload,
        settings=settings,
        provider=getattr(request.app.state, "llm_provider", None),
        checkpointer=getattr(request.app.state, "checkpointer", None),
    )
    return _to_response(run)


@router.post("/agent-runs/{run_id}/messages", response_model=AgentRunResponse)
async def resume_agent_run(
    run_id: uuid.UUID,
    payload: AgentRunMessageRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AgentRunResponse:
    """澄清回答并恢复（API-06）：同 thread_id 从 Checkpoint 恢复（FR-17 / AC-03）。"""
    run, project_id = await generation_service.load_run(session, run_id)
    await _require_membership(request, session, project_id)
    settings = request.app.state.settings
    run = await generation_service.resume_run(
        session,
        run=run,
        answer=payload.answer,
        settings=settings,
        provider=getattr(request.app.state, "llm_provider", None),
        checkpointer=getattr(request.app.state, "checkpointer", None),
    )
    return _to_response(run)


@router.post(
    "/pipeline-versions/{version_id}/freeze", response_model=PipelineVersionFreezeResponse
)
async def freeze_pipeline_version(
    version_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> PipelineVersionFreezeResponse:
    """返回已冻结的制品摘要（API-07）。

    冻结动作发生在门禁通过时（`generation.nodes.gate` → `artifacts.freeze_version_artifacts`），
    本端点是幂等读取口：版本尚无摘要说明从未通过门禁 → 409（状态机不允许）。
    """
    version, project_id = await artifact_service.load_version_with_project(session, version_id)
    await _require_membership(request, session, project_id)
    if not version.artifact_sha256:
        raise StateConflict("版本尚未通过门禁冻结，无可返回的 SHA256")
    return PipelineVersionFreezeResponse(
        id=version.id,
        artifact_sha256=version.artifact_sha256,
        status=version.status,
    )
