"""M-K/T-14 调度 — API 路由（管线调度策略 CRUD）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-K / 第 5.2 章 pipeline_schedules / 第 8 章「安全 · 多租户」
覆盖 FR：FR-40（调度策略配置：cron + 时区 + 模式 + misfire）、FR-41（水印增量契约）、FR-66（项目隔离）
对应 AC：AC-06（调度策略可治理）、AC-12（多租户隔离）

鉴权：全部端点走项目成员校验（`require_project_member`），跨项目一律 404 / 403，
不泄露资源存在性（AC-12）。调度策略的**执行**不由本路由触发——到期触发在 Beat
调度扫描里经 Harness 准备单通道（FR-34：副作用唯一出口）。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.actor_resolver import ActorContext
from etl_agent.auth.dependencies import require_project_member
from etl_agent.infrastructure.database import get_session
from etl_agent.infrastructure.models import PipelineSchedule
from etl_agent.scheduling import service as scheduling_service
from etl_agent.scheduling.schemas import ScheduleResponse, ScheduleUpsertRequest

router = APIRouter(prefix="/api/v1", tags=["scheduling"])


def _view(schedule: PipelineSchedule) -> ScheduleResponse:
    """策略行 → 响应视图（逐字段显式列出，避免顺手带出未登记列）。"""
    return ScheduleResponse(
        id=schedule.id,
        pipeline_id=schedule.pipeline_id,
        cron=schedule.cron,
        timezone=schedule.timezone,
        mode=schedule.mode,
        misfire_policy=schedule.misfire_policy,
        misfire_grace_seconds=schedule.misfire_grace_seconds,
        watermark_column=schedule.watermark_column,
        watermark_initial=schedule.watermark_initial,
        watermark_lag_seconds=schedule.watermark_lag_seconds,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


@router.put(
    "/projects/{project_id}/pipelines/{pipeline_id}/schedule",
    response_model=ScheduleResponse,
)
async def upsert_schedule(
    project_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    payload: ScheduleUpsertRequest,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> ScheduleResponse:
    """创建 / 更新调度策略（FR-40/41）：cron 与时区非法即 400，不落库。"""
    schedule = await scheduling_service.upsert_schedule(
        session,
        project_id=project_id,
        pipeline_id=pipeline_id,
        payload=scheduling_service.ScheduleInput(
            cron=payload.cron,
            timezone=payload.timezone,
            mode=payload.mode,
            misfire_policy=payload.misfire_policy,
            misfire_grace_seconds=payload.misfire_grace_seconds,
            watermark_column=payload.watermark_column,
            watermark_initial=payload.watermark_initial,
            watermark_lag_seconds=payload.watermark_lag_seconds,
        ),
    )
    await session.commit()
    # 更新路径下 `updated_at` 由服务端 `onupdate` 生成，提交后该属性处于过期状态：
    # 异步会话里惰性加载会抛 MissingGreenlet，故显式 refresh 后再构造响应
    await session.refresh(schedule)
    return _view(schedule)


@router.get("/projects/{project_id}/schedules", response_model=list[ScheduleResponse])
async def list_schedules(
    project_id: uuid.UUID,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> list[ScheduleResponse]:
    """列出项目内调度策略（FR-40）。"""
    schedules = await scheduling_service.list_schedules(session, project_id=project_id)
    return [_view(item) for item in schedules]


@router.get(
    "/projects/{project_id}/pipelines/{pipeline_id}/schedule",
    response_model=ScheduleResponse,
)
async def get_schedule(
    project_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> ScheduleResponse:
    """取单条调度策略（不存在即 404，不泄露跨项目存在性）。"""
    schedule = await scheduling_service.load_schedule(
        session, project_id=project_id, pipeline_id=pipeline_id
    )
    return _view(schedule)


@router.delete(
    "/projects/{project_id}/pipelines/{pipeline_id}/schedule",
    status_code=204,
)
async def delete_schedule(
    project_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> None:
    """删除调度策略（FR-40）。"""
    await scheduling_service.delete_schedule(
        session, project_id=project_id, pipeline_id=pipeline_id
    )
    await session.commit()
