"""M-K 执行与数据面 — ExecutionRun 状态机（QUEUED→SUBMITTED→RUNNING→终态）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 5.2 章 execution_runs / 第 3.2 章 M-K / 第 6.1 章 API-12
覆盖 FR：FR-39（运行管理与监控：七态状态机 + 每次迁移写事件流）
对应 AC：AC-06（Worker 推进执行状态可观测）、AC-07 前置（状态与事件流供运行中心查询）

状态机（SPEC 5.2 execution_runs.status 注释）：
    QUEUED → SUBMITTED → RUNNING → SUCCEEDED
                             ↘ FAILED
                    ↘ CANCELLING → CANCELLED
- 终态（SUCCEEDED/FAILED/CANCELLED）不可再迁移；
- CANCELLING 只允许到 CANCELLED（取消确认）或 FAILED（取消期间引擎失败）；
- 非法迁移抛 `ExecutionStateConflict`，且不写任何事件（失败不留半迁移现场）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution.errors import ExecutionNotFound, ExecutionStateConflict
from etl_agent.infrastructure.models import (
    ExecutionEvent,
    ExecutionRun,
    Pipeline,
    PipelineVersion,
)

STATUS_QUEUED = "QUEUED"
STATUS_SUBMITTED = "SUBMITTED"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
STATUS_CANCELLING = "CANCELLING"
STATUS_CANCELLED = "CANCELLED"

ALL_STATUSES: tuple[str, ...] = (
    STATUS_QUEUED,
    STATUS_SUBMITTED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    STATUS_FAILED,
    STATUS_CANCELLING,
    STATUS_CANCELLED,
)

# 终态集合（FR-39：终态运行不可再推进，查询与回滚入口按此判活）
TERMINAL_STATUSES: frozenset[str] = frozenset({STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED})

# 迁移白名单（跳跃迁移一律拒绝，如 QUEUED→RUNNING）
#
# SUBMITTED → CANCELLING 是合法边：进入 SUBMITTED 即意味着引擎作业已提交
# （`seatunnel_job_id` 已写入），取消同样需要「申请 → 等引擎终止确认」两段式——
# 若只允许 SUBMITTED → CANCELLED 一步到位，运行会在引擎侧仍在收尾时被标记为已终止。
TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_QUEUED: frozenset({STATUS_SUBMITTED, STATUS_FAILED, STATUS_CANCELLED}),
    STATUS_SUBMITTED: frozenset(
        {STATUS_RUNNING, STATUS_FAILED, STATUS_CANCELLING, STATUS_CANCELLED}
    ),
    STATUS_RUNNING: frozenset(
        {STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLING, STATUS_CANCELLED}
    ),
    STATUS_CANCELLING: frozenset({STATUS_CANCELLED, STATUS_FAILED}),
    STATUS_SUCCEEDED: frozenset(),
    STATUS_FAILED: frozenset(),
    STATUS_CANCELLED: frozenset(),
}


def can_transition(current: str, target: str) -> bool:
    """状态机白名单判定（跳跃迁移 / 原地迁移均为 False）。"""
    return target in TRANSITIONS.get(current, frozenset())


def is_terminal(status: str) -> bool:
    """是否终态（供扫描器判活：非终态运行才继续推进）。"""
    return status in TERMINAL_STATUSES


async def load_run(session: AsyncSession, run_id: uuid.UUID) -> ExecutionRun:
    """加载执行运行（不存在即 404；跨项目由调用方经项目反查后判定）。

    Raises:
        ExecutionNotFound: 运行不存在。
    """
    run = await session.get(ExecutionRun, run_id)
    if run is None:
        raise ExecutionNotFound("执行运行不存在")
    return run


async def load_run_project(
    session: AsyncSession, run_id: uuid.UUID
) -> tuple[ExecutionRun, uuid.UUID]:
    """反查运行所属项目（API-12/14 路径无 `project_id`，需回溯项目做鉴权）。

    运行 → 版本 → 管线 → 项目四级回溯：执行运行没有 project_id 列（SPEC 5.2），
    项目归属是派生事实，必须沿外键链取，不能靠调用方声明。

    Raises:
        ExecutionNotFound: 运行不存在（不泄露存在性）。
    """
    result = await session.execute(
        select(ExecutionRun, Pipeline.project_id)
        .join(PipelineVersion, ExecutionRun.pipeline_version_id == PipelineVersion.id)
        .join(Pipeline, PipelineVersion.pipeline_id == Pipeline.id)
        .where(ExecutionRun.id == run_id)
    )
    row = result.one_or_none()
    if row is None:
        raise ExecutionNotFound("执行运行不存在")
    return row[0], row[1]


async def load_run_by_preparation(
    session: AsyncSession, preparation_id: uuid.UUID
) -> ExecutionRun | None:
    """按准备单取运行（一准备单一运行，SPEC 5.2 `harness_preparation_id` UK）。"""
    return (
        (
            await session.execute(
                select(ExecutionRun).where(ExecutionRun.harness_preparation_id == preparation_id)
            )
        )
        .scalars()
        .first()
    )


async def list_runs_for_project(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[tuple[ExecutionRun, str | None]], int]:
    """按项目列出执行运行（API-12 列表；项目隔离沿外键链派生）。

    执行运行没有 `project_id` 列（SPEC 5.2），项目归属沿
    `ExecutionRun → PipelineVersion → Pipeline.project_id` 派生——与
    `load_run_with_project` 同一口径，绝不按调用方声明过滤（FR-66 / AC-12）。

    Args:
        session: 请求级数据库会话。
        project_id: 项目隔离边界。
        status: 可选状态过滤（七态之一；非法值由路由层先行校验）。
        limit: 单页条数（路由层已 clamp 到 1..200）。
        offset: 偏移（分页）。

    Returns:
        (rows, total)：rows 为 (运行, pipeline 名) 列表，按 `submitted_at` 倒序
        （NULL 排在最后），同刻以 `id` 兜底保证稳定序；total 为同过滤条件下的总数
        （分页前的全量，供前端显示与翻页）。
    """
    base = (
        select(ExecutionRun, Pipeline.name)
        .join(PipelineVersion, ExecutionRun.pipeline_version_id == PipelineVersion.id)
        .join(Pipeline, PipelineVersion.pipeline_id == Pipeline.id)
        .where(Pipeline.project_id == project_id)
    )
    if status is not None:
        base = base.where(ExecutionRun.status == status)

    total = (
        await session.execute(
            select(func.count()).select_from(base.subquery()),
        )
    ).scalar_one()

    rows = (
        await session.execute(
            base.order_by(
                ExecutionRun.submitted_at.desc().nullslast(),
                ExecutionRun.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [(row[0], row[1]) for row in rows], int(total)


async def record_event(
    session: AsyncSession,
    run_id: uuid.UUID,
    *,
    level: str,
    event_type: str,
    message: str,
    stage: str | None = None,
    occurred_at: datetime | None = None,
) -> ExecutionEvent:
    """写一条执行事件（FR-39：事件流随每次状态迁移落库）。

    Args:
        level: INFO / WARN / ERROR。
        event_type: 事件类型标识（如 execution.submitted）。
        message: 人类可读摘要（**禁止包含凭据 / HOCON 正文 / Prompt**，AC-02）。
        stage: 执行阶段（prepare / submit / run / finalize 等）。
        occurred_at: 事件时间；缺省当前 UTC。
    """
    event = ExecutionEvent(
        run_id=run_id,
        level=level,
        stage=stage,
        event_type=event_type,
        message=message,
        occurred_at=occurred_at or datetime.now(UTC),
    )
    session.add(event)
    await session.flush()
    return event


async def transition(
    session: AsyncSession,
    run: ExecutionRun,
    target: str,
    *,
    reason: str | None = None,
    failure_category: str | None = None,
    failure_stage: str | None = None,
    retryable: bool | None = None,
    diagnosis: str | None = None,
) -> ExecutionRun:
    """推进执行状态并同事务写事件流（FR-39）。

    每一次合法迁移都会：① 更新 `status` 与阶段时间戳（submitted_at / started_at /
    finished_at）；② 刷新 `last_observed_at`；③ 落一条 ExecutionEvent。

    Args:
        target: 目标状态（白名单内）。
        reason: 迁移原因（取消 / 失败诊断等；只写治理事实，不写凭据）。
        failure_category / failure_stage / retryable / diagnosis:
            失败迁移时记录的失败分类（S7 记录字段，S8 诊断引擎补根因建议）。

    Raises:
        ExecutionStateConflict: 非法迁移（跳跃 / 终态再迁移 / 原地迁移）。
    """
    current = run.status
    if not can_transition(current, target):
        raise ExecutionStateConflict(
            f"执行状态 {current} 不可迁移到 {target}（FR-39 状态机白名单）"
        )
    now = datetime.now(UTC)
    run.status = target
    run.last_observed_at = now
    if target == STATUS_SUBMITTED:
        run.submitted_at = now
    elif target == STATUS_RUNNING:
        run.started_at = now
    elif target in {STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED}:
        run.finished_at = now
    if failure_category is not None:
        run.failure_category = failure_category
    if failure_stage is not None:
        run.failure_stage = failure_stage
    if retryable is not None:
        run.retryable = retryable
    if diagnosis is not None:
        run.diagnosis = diagnosis
    await session.flush()
    await record_event(
        session,
        run.id,
        level="ERROR" if target == STATUS_FAILED else "INFO",
        event_type=f"execution.{target.lower()}",
        message=reason or f"执行迁移 {current} → {target}",
        stage=stage_of(target),
    )
    return run


def stage_of(status: str) -> str:
    """状态到阶段的映射（事件流 stage 字段，FR-39）。"""
    return {
        STATUS_QUEUED: "prepare",
        STATUS_SUBMITTED: "submit",
        STATUS_RUNNING: "run",
        STATUS_SUCCEEDED: "finalize",
        STATUS_FAILED: "finalize",
        STATUS_CANCELLING: "cancel",
        STATUS_CANCELLED: "cancel",
    }.get(status, "run")
