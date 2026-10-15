"""M-L 运行时监督 — Worker 监督快照周期任务（10s，FR-45，AC-08 核心）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 4.3 章进程与入口 /
第 5.2 章 runtime_supervision_snapshots / 第 8 章「性能 · 运行时预算硬上限」
覆盖 FR：FR-45（周期快照 + 6 条规则判定）、FR-46（减损动作 + 硬中断 + 隔离）
对应 AC：AC-08（超预算 → 硬中断 + 隔离 + 根因诊断）

任务薄壳：开会话 → `run_supervision_cycle` → 提交。周期与心跳同频（10s，可配）。

**挂点选在 `_finalize_run` 之后**（S7 吸收清单 3 / 9）：本任务**不重写**执行推进逻辑，
只对**运行中的**运行做判决与减损；终态运行由 S7 的 `_finalize_run` 收口，
其质量报告（PASSED/WARNING/FAILED/INCOMPLETE）是发布门禁的输入。

判决 → 动作的映射（提示词 §8.4 / §8.7）：

```
命中规则 → mitigation.plan_mitigation 聚合出最严动作
  CONTINUE   → 写快照（无事件）
  THROTTLE   → 写快照（提高监控频率，软动作）
  PAUSE      → 写快照 + 外部动作 PAUSE_ENGINE_JOB（独立重试）
  CANCEL     → 写快照 → interrupt.hard_interrupt（迁状态 → stop-job → CANCELLED → 诊断）
  QUARANTINE → 写快照 + quarantine.quarantine_run（阻断发布与原子 Swap）
```
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution.service import STATUS_CANCELLING, STATUS_RUNNING, STATUS_SUBMITTED
from etl_agent.infrastructure.models import ExecutionRun, QualityReport
from etl_agent.supervision.contracts import (
    SupervisionContract,
    SupervisionObservation,
    evaluate,
)
from etl_agent.supervision.mitigation import (
    ACTION_CANCEL,
    ACTION_PAUSE,
    ACTION_QUARANTINE,
    plan_mitigation,
)
from etl_agent.supervision.snapshot import record_supervision_event, write_snapshot
from etl_agent.worker.celery_app import app

# 单遍最多监督的运行数（防止一次扫全表；监督是周期任务，可跨遍摊平）
MAX_SUPERVISE_PER_SCAN = 200

# 可被监督判决的运行状态（终态不再监督：CANCELLED/SUCCEEDED/FAILED 已是定局）
SUPERVISABLE_STATUSES: tuple[str, ...] = (
    STATUS_RUNNING,
    STATUS_SUBMITTED,
    STATUS_CANCELLING,
)


@dataclass
class SupervisionCycleResult:
    """一遍监督的结论（供任务返回与运行中心展示）。"""

    evaluated: int = 0
    cancelled: int = 0
    quarantined: int = 0
    paused: int = 0
    warned: int = 0
    skipped: int = 0
    details: list[str] = field(default_factory=list)


def observation_of(run: ExecutionRun, report: QualityReport | None) -> SupervisionObservation:
    """由运行事实 + 质量报告构造监督观测（**只含聚合指标**，不含业务数据）。

    - `records`：取源 / 目标行数的较大者（放大比的分母用源行数，见下）；
    - `bytes_total`：源 + 目标字节；
    - `duration_seconds`：`started_at` 至当前（未开始则 None）；
    - `amplification_ratio`：`sink_rows / source_rows`（源为 0 时不可算 → None）；
    - `reject_rate`：质量报告的拒绝率（无报告则 None）。
    """
    source_rows = run.source_rows
    sink_rows = run.sink_rows
    records = max([value for value in (source_rows, sink_rows) if value is not None], default=None)
    bytes_total: int | None = None
    if run.source_bytes is not None or run.sink_bytes is not None:
        bytes_total = (run.source_bytes or 0) + (run.sink_bytes or 0)
    duration: float | None = None
    if run.started_at is not None:
        started = run.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        duration = max((datetime.now(UTC) - started).total_seconds(), 0.0)
    amplification: float | None = None
    if source_rows and sink_rows is not None:
        amplification = sink_rows / source_rows
    reject_rate = report.reject_rate if report is not None else None
    return SupervisionObservation(
        records=records,
        bytes_total=bytes_total,
        duration_seconds=duration,
        amplification_ratio=amplification,
        reject_rate=reject_rate,
        quality_rows=report.total_rows if report is not None else None,
    )


async def supervise_run(
    session: AsyncSession,
    run: ExecutionRun,
    *,
    contract: SupervisionContract,
    report: QualityReport | None,
    engine: object | None = None,
    max_attempts: int | None = None,
) -> str:
    """对单个运行做一次监督判决与减损（返回落定的动作）。

    顺序：评估 → 写快照 → 写事件 → 执行减损动作（QUARANTINE 先于 CANCEL 记录，
    因为隔离是发布侧红线，必须在硬中断之前落库，防止中断期间发布链路趁虚而入）。
    """
    from etl_agent.supervision.external_mitigation import DEFAULT_MAX_ATTEMPTS
    from etl_agent.supervision.interrupt import hard_interrupt
    from etl_agent.supervision.quarantine import quarantine_run

    observation = observation_of(run, report)
    outcome = evaluate(contract, observation)
    await write_snapshot(session, run, outcome=outcome, observation=observation)
    await record_supervision_event(session, run, outcome=outcome, observation=observation)

    plan = plan_mitigation(outcome.matches)
    if plan.quarantines:
        await quarantine_run(session, run, reason=f"监督判定：{outcome.risk_level} 规则命中")
    if plan.stops_engine:
        await hard_interrupt(
            session,
            run,
            engine=engine,
            reason=f"监督判定：命中 {', '.join(outcome.rule_ids)}",
            max_attempts=max_attempts or DEFAULT_MAX_ATTEMPTS,
        )
    elif ACTION_PAUSE in plan.actions:
        await _pause_engine(session, run, engine=engine, max_attempts=max_attempts)
    return outcome.action


async def _pause_engine(
    session: AsyncSession,
    run: ExecutionRun,
    *,
    engine: object | None,
    max_attempts: int | None,
) -> None:
    """PAUSE 外部动作（独立重试；失败不阻塞快照，禁止事项 18）。"""
    from etl_agent.supervision.external_mitigation import (
        DEFAULT_MAX_ATTEMPTS,
        apply_with_retry,
        not_required,
        record_external_mitigation,
    )

    job_id = run.seatunnel_job_id
    if engine is None or not job_id:
        result = not_required("PAUSE_ENGINE_JOB")
    else:

        async def _call() -> None:
            await engine.pause_job(job_id)  # type: ignore[attr-defined]

        result = await apply_with_retry(
            "PAUSE_ENGINE_JOB", _call, max_attempts=max_attempts or DEFAULT_MAX_ATTEMPTS
        )
    await record_external_mitigation(session, run.id, result)


async def run_supervision_cycle(
    session: AsyncSession,
    *,
    settings: object | None = None,
    engine: object | None = None,
    now: datetime | None = None,
) -> SupervisionCycleResult:
    """一遍监督：取运行中的运行 → 逐条判决与减损（AC-08）。

    契约由运行自身的 `supervision_contract_sha256` 对账（S8 只按冻结默认预算判定，
    不读准备单正文——监督不得被准备单里的自定义预算放宽，禁止事项 14）。
    """
    _ = now
    runs = (
        (
            await session.execute(
                select(ExecutionRun)
                .where(ExecutionRun.status.in_(list(SUPERVISABLE_STATUSES)))
                .order_by(ExecutionRun.started_at)
                .limit(MAX_SUPERVISE_PER_SCAN)
            )
        )
        .scalars()
        .all()
    )
    contract = SupervisionContract.frozen()
    result = SupervisionCycleResult()
    for run in runs:
        report = await _latest_report(session, run.id)
        action = await supervise_run(
            session, run, contract=contract, report=report, engine=engine
        )
        result.evaluated += 1
        if action == ACTION_CANCEL:
            result.cancelled += 1
            result.details.append(f"run {run.id}: CANCEL")
        elif action == ACTION_QUARANTINE:
            result.quarantined += 1
            result.details.append(f"run {run.id}: QUARANTINE")
        elif action == ACTION_PAUSE:
            result.paused += 1
            result.details.append(f"run {run.id}: PAUSE")
        elif action != "CONTINUE":
            result.warned += 1
            result.details.append(f"run {run.id}: {action}")
        else:
            result.skipped += 1
    await session.flush()
    return result


async def _latest_report(session: AsyncSession, run_id: object) -> QualityReport | None:
    """取运行的最新质量报告（`run_id` 唯一，至多一行）。"""
    return (
        (
            await session.execute(
                select(QualityReport).where(QualityReport.run_id == run_id)
            )
        )
        .scalars()
        .first()
    )


async def _run() -> dict[str, object]:
    from etl_agent.config import settings
    from etl_agent.worker.seatunnel_client import RestSeaTunnelClient
    from etl_agent.worker.session import worker_session

    engine = RestSeaTunnelClient(
        settings.seatunnel_url,
        submit_timeout_seconds=settings.seatunnel_submit_timeout_seconds,
        check_timeout_seconds=settings.seatunnel_check_timeout_seconds,
    )
    async with worker_session() as session:
        summary = await run_supervision_cycle(session, settings=settings, engine=engine)
        await session.commit()
        return {
            "evaluated": summary.evaluated,
            "cancelled": summary.cancelled,
            "quarantined": summary.quarantined,
            "paused": summary.paused,
            "warned": summary.warned,
            "skipped": summary.skipped,
            "details": summary.details,
        }


@app.task(name="worker.supervise", bind=True, acks_late=True)
def supervise_task(self: object) -> dict[str, object]:
    """Celery 入口（Beat 10s；返回本遍监督计数摘要）。"""
    return asyncio.run(_run())


__all__ = [
    "MAX_SUPERVISE_PER_SCAN",
    "SUPERVISABLE_STATUSES",
    "SupervisionCycleResult",
    "observation_of",
    "run_supervision_cycle",
    "supervise_run",
    "supervise_task",
]
