"""M-F 生成引擎 — API 路由（API-05 / API-06）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-05/06 / 第 6.2 章接口契约 / 第 8 章「安全 · 多租户」
覆盖 FR：FR-16（创建运行）、FR-17（澄清恢复）、FR-66（项目隔离）
对应 AC：AC-03（中断与恢复）、AC-12（跨项目 403）

S6 变更：API-07（版本冻结）已迁至 M-H 版本域 `versions/router.py`（S5 吸收 1：
由幂等读取口升级为写入与状态迁移接口），本文件不再承载版本冻结。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.actor_resolver import ActorContext
from etl_agent.auth.dependencies import require_actor_for_project, require_project_member
from etl_agent.generation import service as generation_service
from etl_agent.generation.schemas import (
    AgentRunCreateRequest,
    AgentRunListView,
    AgentRunMessageRequest,
    AgentRunResponse,
)
from etl_agent.infrastructure.database import get_session
from etl_agent.infrastructure.models import AgentRun, Pipeline, PipelineVersion
from etl_agent.infrastructure.pagination import paginate

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
    """资源回溯项目的成员校验（API-06 无 project_id 路径参数时的等价依赖）。"""
    return await require_actor_for_project(request, session, project_id)


@router.get(
    "/projects/{project_id}/agent-runs",
    response_model=list[AgentRunListView],
)
async def list_agent_runs(
    project_id: uuid.UUID,
    response: Response,
    status: str | None = Query(default=None, description="可选：按运行状态过滤"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> list[AgentRunListView]:
    """列出项目的生成运行（联调修复新增；Studio 消费）。

    项目隔离：`agent_runs` 无 `project_id` 列，经
    `pipeline_version_id → PipelineVersion.pipeline_id → Pipeline.project_id` 派生
    （FR-66 / AC-12）。总数经 `X-Total-Count` 响应头回传，列表体保持裸数组。
    """
    base = (
        select(AgentRun, PipelineVersion.pipeline_id)
        .join(PipelineVersion, AgentRun.pipeline_version_id == PipelineVersion.id)
        .join(Pipeline, PipelineVersion.pipeline_id == Pipeline.id)
        .where(Pipeline.project_id == project_id)
    )
    if status is not None:
        base = base.where(AgentRun.status == status)
    rows, total = await paginate(
        session,
        base,
        order_by=(AgentRun.created_at.desc(), AgentRun.id.desc()),
        limit=limit,
        offset=offset,
    )
    response.headers["X-Total-Count"] = str(total)
    return [
        AgentRunListView(
            id=run.id,
            thread_id=run.thread_id,
            pipeline_id=pipeline_id,
            status=run.status,
            repair_count=run.generation_repair_count or 0,
            clarify_question=run.pending_question,
            created_at=run.created_at,
        )
        for run, pipeline_id in rows
    ]


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
