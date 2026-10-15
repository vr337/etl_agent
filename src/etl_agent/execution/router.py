"""M-K 执行与数据面 — API 路由（API-11 触发执行 / API-12 查询执行 / API-14 质量报告）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-11/12/14 / 第 6.2 章接口契约 / 第 8 章「安全 · 多租户」
覆盖 FR：FR-38（触发受管执行，Operator）、FR-39（状态 / 事件流 / 行数字节）、FR-44（质量报告）、FR-66（项目隔离）
对应 AC：AC-06（执行可触发可查询）、AC-12（跨项目 404 / 403）、AC-02（响应零明文）

**API-11 的语义裁决**（实现口径，见阶段报告「执行偏差」）：
`Commit`（API-10）已经签发令牌并同事务写入 `ExecutionRun(QUEUED)` + `Outbox(PENDING)`；
因此「触发受管执行」不是再授权，而是**点火**——让这条已授权的命令不必等 Beat 的 2s 周期。
本路由把一次「立即扫描」派发到 Broker（Celery 任务 `worker.scan_outbox`），
**不在 API 进程内执行引擎调用**（SPEC 3.2：Worker 才是执行通道）。
派发失败不阻塞响应：返回 `dispatched=false`，由 Beat 周期兜底领取（FR-35 至少一次投递）。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.actor_resolver import ActorContext
from etl_agent.auth.dependencies import require_actor_for_project, require_project_member
from etl_agent.auth.errors import RoleRequired, ValidationError
from etl_agent.execution import events as events_module
from etl_agent.execution import service as execution_service
from etl_agent.execution.errors import ExecutionNotFound, ExecutionStateConflict
from etl_agent.execution.schemas import (
    ExecutionDetailResponse,
    ExecutionEventView,
    ExecutionRunView,
    ExecutionTriggerRequest,
    ExecutionTriggerResponse,
    QualityReportView,
)
from etl_agent.infrastructure.database import get_session
from etl_agent.infrastructure.models import (
    ExecutionPreparation,
    ExecutionRun,
    QualityReport,
)

logger = logging.getLogger("etl_agent.execution.router")

router = APIRouter(prefix="/api/v1", tags=["execution"])

# 准备单可触发执行的状态（S6 状态机：COMMITTED 才意味着单次令牌已签发）
STATUS_COMMITTED = "COMMITTED"

ALLOWED_TRIGGERS: frozenset[str] = frozenset({"MANUAL", "SCHEDULED"})


def _run_view(run: ExecutionRun) -> ExecutionRunView:
    """运行行 → 响应视图（逐字段显式列出，避免顺手带出未登记列）。"""
    return ExecutionRunView(
        id=run.id,
        pipeline_version_id=run.pipeline_version_id,
        harness_preparation_id=run.harness_preparation_id,
        status=run.status,
        trigger=run.trigger,
        seatunnel_job_id=run.seatunnel_job_id,
        engine_status=run.engine_status,
        scheduled_for=run.scheduled_for,
        source_rows=run.source_rows,
        sink_rows=run.sink_rows,
        source_bytes=run.source_bytes,
        sink_bytes=run.sink_bytes,
        table_metrics=run.table_metrics,
        retry_of_run_id=run.retry_of_run_id,
        attempt_number=run.attempt_number,
        failure_category=run.failure_category,
        failure_stage=run.failure_stage,
        retryable=run.retryable,
        diagnosis=run.diagnosis,
        submitted_at=run.submitted_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        last_observed_at=run.last_observed_at,
    )


def _dispatch_scan(request: Request) -> bool:
    """派发一次即时 Outbox 扫描（失败不抛出：Beat 周期兜底，FR-35）。"""
    dispatcher: Callable[[], None] | None = getattr(request.app.state, "execution_dispatcher", None)
    try:
        (dispatcher or _celery_dispatch)()
        return True
    except Exception:
        logger.warning("即时派发扫描失败，等待 Beat 周期领取（FR-35 至少一次投递）", exc_info=True)
        return False


def _celery_dispatch() -> None:
    """默认派发通道：向 Broker 投递一条扫描任务（生产路径）。"""
    from etl_agent.worker.beat_schedule import TASK_SCAN_OUTBOX
    from etl_agent.worker.celery_app import app as celery_app

    celery_app.send_task(TASK_SCAN_OUTBOX)


@router.post(
    "/projects/{project_id}/executions",
    response_model=ExecutionTriggerResponse,
    status_code=202,
)
async def trigger_execution(
    project_id: uuid.UUID,
    payload: ExecutionTriggerRequest,
    request: Request,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> ExecutionTriggerResponse:
    """API-11：触发受管执行（仅 OPERATOR；点火已授权的 Commit 结果）。

    前置事实（全部服务端校验，不信调用方声明）：
    1. 准备单属于本项目且状态为 `COMMITTED`（未 Commit 即没有单次令牌，无从执行）；
    2. 对应运行存在且非终态（终态运行不可重新触发，重试走 FR-42 新建运行）；
    3. 可选校验 `pipeline_version_id` 与准备单登记值一致（防串版本）。

    Raises:
        RoleRequired:              操作者不是 OPERATOR（AUTH_403_ROLE_REQUIRED）。
        ValidationError:           `trigger` 取值非法（BIZ_400_VALIDATION）。
        ExecutionNotFound:         准备单 / 运行不存在或跨项目（BIZ_404_*）。
        ExecutionStateConflict:    准备单未 Commit / 运行已终态 / 版本不一致（BIZ_409_*）。
    """
    if "OPERATOR" not in actor.harness_roles:
        raise RoleRequired("需要 Harness 职责: OPERATOR（API-11 仅操作员可触发执行）")
    if payload.trigger not in ALLOWED_TRIGGERS:
        raise ValidationError(
            f"trigger 取值非法：{payload.trigger!r}，可选 {sorted(ALLOWED_TRIGGERS)}"
        )

    preparation = await _load_preparation(session, payload.preparation_id, project_id)
    if preparation.status != STATUS_COMMITTED:
        raise ExecutionStateConflict(
            f"准备单状态 {preparation.status}：未 Commit 则无单次令牌，不可触发执行（FR-33）"
        )
    if (
        payload.pipeline_version_id is not None
        and payload.pipeline_version_id != preparation.pipeline_version_id
    ):
        raise ExecutionStateConflict("pipeline_version_id 与准备单登记版本不一致（FR-31）")

    run = await execution_service.load_run_by_preparation(session, preparation.id)
    if run is None:
        raise ExecutionNotFound("该准备单尚无执行运行（Commit 事务未完成）")
    if execution_service.is_terminal(run.status):
        raise ExecutionStateConflict(
            f"执行已终态 {run.status}：重试需新建运行（FR-42），不可重复触发"
        )
    run.trigger = payload.trigger
    await session.commit()

    dispatched = _dispatch_scan(request)
    return ExecutionTriggerResponse(run=_run_view(run), dispatched=dispatched)


@router.get("/executions/{run_id}", response_model=ExecutionDetailResponse)
async def get_execution(
    run_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ExecutionDetailResponse:
    """API-12：执行状态 + 事件流 + 行数 / 字节 + 监督快照（S7 恒 None）。

    监督快照属 M-L（S8 落 `runtime_supervision_snapshots`）：S7 不做监督，
    字段保留为 None 而不是省略——契约字段恒定存在，前端无需按版本分支。
    """
    run, project_id = await execution_service.load_run_project(session, run_id)
    await require_actor_for_project(request, session, project_id)
    events = await events_module.list_events(session, run.id)
    base = _run_view(run)
    return ExecutionDetailResponse(
        **base.model_dump(),
        events=[
            ExecutionEventView(
                level=item.level,
                stage=item.stage,
                event_type=item.event_type,
                message=item.message,
                occurred_at=item.occurred_at,
            )
            for item in events
        ],
        supervision_snapshot=None,
    )


@router.get("/executions/{run_id}/quality-report", response_model=QualityReportView)
async def get_quality_report(
    run_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> QualityReportView:
    """API-14：质量契约与分流报告（FR-44）。

    报告在引擎作业终态时生成（Worker 侧），未终态即 404——「还没有报告」与
    「报告为空」是两件事，用 404 而不是空对象表达，避免前端把未完成当成通过。
    """
    run, project_id = await execution_service.load_run_project(session, run_id)
    await require_actor_for_project(request, session, project_id)
    report = (
        (await session.execute(select(QualityReport).where(QualityReport.run_id == run.id)))
        .scalars()
        .first()
    )
    if report is None:
        raise ExecutionNotFound("质量报告尚未生成（执行未达终态）")
    return QualityReportView(
        run_id=report.run_id,
        status=report.status,
        total_rows=report.total_rows,
        passed_rows=report.passed_rows,
        rejected_rows=report.rejected_rows,
        reject_rate=report.reject_rate,
        valid_sink=report.valid_sink,
        error_sink=report.error_sink,
        rules=list(report.rules or []),
        diagnostics=list(report.diagnostics or []),
        generated_at=report.generated_at,
    )


async def _load_preparation(
    session: AsyncSession, preparation_id: uuid.UUID, project_id: uuid.UUID
) -> ExecutionPreparation:
    """按项目边界加载准备单（跨项目视同不存在，AC-12）。

    Raises:
        ExecutionNotFound: 准备单不存在或不属于本项目。
    """
    preparation = (
        (
            await session.execute(
                select(ExecutionPreparation).where(
                    ExecutionPreparation.id == preparation_id,
                    ExecutionPreparation.project_id == project_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if preparation is None:
        raise ExecutionNotFound("准备单不存在或不属于当前项目")
    return preparation
