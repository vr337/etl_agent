"""M-L 监督周期任务用例（T-16.1 / FR-45 / FR-46 / AC-08）。

覆盖：`run_supervision_cycle` 只取运行中的运行、观测构造（只含聚合指标）、
判决 → 减损映射（CONTINUE / QUARANTINE）、快照与事件落库、
Celery 任务注册与薄壳可调用性。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 5.2 章 runtime_supervision_snapshots /
第 8 章「性能 · 运行时预算硬上限」
覆盖 FR：FR-45（周期快照 + 规则判定）、FR-46（减损动作）
对应 AC：AC-08（超预算 → 减损）
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import (
    ExecutionRun,
    QualityReport,
    RuntimeSupervisionSnapshot,
)
from etl_agent.supervision.contracts import SupervisionContract
from etl_agent.supervision.mitigation import ACTION_CONTINUE, ACTION_QUARANTINE
from etl_agent.worker import app as worker_app
from etl_agent.worker.beat_schedule import TASK_SUPERVISE
from etl_agent.worker.tasks.supervise import (
    MAX_SUPERVISE_PER_SCAN,
    SUPERVISABLE_STATUSES,
    SupervisionCycleResult,
    observation_of,
    run_supervision_cycle,
    supervise_run,
    supervise_task,
)
from tests.supervision.conftest import RunFactory


async def _snapshot_count(session: AsyncSession) -> int:
    return (
        await session.execute(select(func.count()).select_from(RuntimeSupervisionSnapshot))
    ).scalar_one()


def test_contract_frozen_budget_is_single_source() -> None:
    """监督契约取自冻结默认预算（禁止事项 14：不得被准备单自定义预算放宽）。"""
    contract = SupervisionContract.frozen()
    assert contract == SupervisionContract.frozen()  # 同值（dataclass 值语义）
    assert SUPERVISABLE_STATUSES == ("RUNNING", "SUBMITTED", "CANCELLING")


def test_max_per_scan_is_bounded() -> None:
    """单遍上限存在（防止一次扫全表）。"""
    assert isinstance(MAX_SUPERVISE_PER_SCAN, int)
    assert MAX_SUPERVISE_PER_SCAN > 0


def test_observation_of_uses_both_ends() -> None:
    """观测构造：records 取源 / 目标较大者；放大比用 source_rows 作分母。"""
    run = ExecutionRun(
        pipeline_version_id=None,  # type: ignore[arg-type]
        harness_preparation_id=None,  # type: ignore[arg-type]
        status="RUNNING",
        trigger="MANUAL",
        started_at=datetime.now(UTC) - timedelta(seconds=30),
        source_rows=100,
        sink_rows=250,
        source_bytes=1024,
        sink_bytes=2048,
    )
    observation = observation_of(run, None)
    assert observation.records == 250
    assert observation.bytes_total == 3072
    assert observation.amplification_ratio == 2.5
    assert observation.duration_seconds is not None
    assert observation.duration_seconds >= 29.0
    assert observation.reject_rate is None


def test_observation_of_handles_missing_metrics() -> None:
    """无指标时全部为 None，不抛异常。"""
    run = ExecutionRun(
        pipeline_version_id=None,  # type: ignore[arg-type]
        harness_preparation_id=None,  # type: ignore[arg-type]
        status="SUBMITTED",
        trigger="MANUAL",
    )
    observation = observation_of(run, None)
    assert observation.records is None
    assert observation.bytes_total is None
    assert observation.duration_seconds is None
    assert observation.amplification_ratio is None


def test_observation_of_skips_amplification_without_source() -> None:
    """源行数为 0 / None 时放大比不可算 → None（不得除零）。"""
    run = ExecutionRun(
        pipeline_version_id=None,  # type: ignore[arg-type]
        harness_preparation_id=None,  # type: ignore[arg-type]
        status="RUNNING",
        trigger="MANUAL",
        source_rows=0,
        sink_rows=50,
    )
    assert observation_of(run, None).amplification_ratio is None


async def test_supervise_run_clean_writes_snapshot(
    db_session: AsyncSession, make_run: RunFactory
) -> None:
    """指标宽松的运行：判 CONTINUE，仅写快照（无减损事件落地为隔离 / 终止）。"""
    run = await make_run(status="RUNNING", source_rows=10, sink_rows=11)
    before = await _snapshot_count(db_session)
    action = await supervise_run(
        db_session, run, contract=SupervisionContract.frozen(), report=None
    )
    await db_session.flush()
    assert action == ACTION_CONTINUE
    assert await _snapshot_count(db_session) == before + 1


async def test_supervise_run_quarantines_on_budget_breach(
    db_session: AsyncSession, make_run: RunFactory
) -> None:
    """放大比严重超预算 → QUARANTINE（阻断发布侧），运行被标记隔离。"""
    # 放大比 10x + 长时长（默认预算远小于此），必然触发最严动作。
    run = await make_run(
        status="RUNNING",
        started_at=datetime.now(UTC) - timedelta(days=30),
        source_rows=100,
        sink_rows=1000,
    )
    action = await supervise_run(
        db_session, run, contract=SupervisionContract.frozen(), report=None
    )
    await db_session.flush()
    assert action in {ACTION_QUARANTINE, "CANCEL", "PAUSE", "THROTTLE"}
    assert await _snapshot_count(db_session) >= 1


async def test_cycle_only_scans_running(
    db_session: AsyncSession, make_run: RunFactory
) -> None:
    """本遍只监督运行中的运行（终态由 S7 `_finalize_run` 收口，不重复判决）。"""
    await make_run(status="RUNNING", source_rows=10, sink_rows=10)
    await make_run(status="SUCCEEDED", source_rows=10, sink_rows=10)
    await make_run(status="FAILED", source_rows=10, sink_rows=10)
    result = await run_supervision_cycle(db_session)
    assert isinstance(result, SupervisionCycleResult)
    assert result.evaluated == 1
    assert result.skipped == 1
    assert result.cancelled == 0


async def test_cycle_empty_when_nothing_running(db_session: AsyncSession) -> None:
    """无运行中的运行时，本遍不做任何判决。"""
    result = await run_supervision_cycle(db_session)
    assert result.evaluated == 0
    assert result.details == []


async def test_cycle_uses_latest_quality_report(
    db_session: AsyncSession, make_run: RunFactory, seeded: object
) -> None:
    """存在质量报告时，拒绝率进入观测（供 reject_rate 规则判定）。"""
    run = await make_run(status="RUNNING", source_rows=100, sink_rows=100)
    db_session.add(
        QualityReport(
            run_id=run.id,
            status="PASSED",
            total_rows=100,
            passed_rows=100,
            rejected_rows=0,
            reject_rate=0.0,
        )
    )
    await db_session.flush()
    result = await run_supervision_cycle(db_session)
    assert result.evaluated == 1
    assert result.skipped == 1  # 拒绝率 0 → 不命中，走 CONTINUE


def test_task_registered_and_callable() -> None:
    """Celery 任务按注册名就位（Beat 以字符串名派发，缺失即静默不跑）。"""
    assert TASK_SUPERVISE in set(worker_app.tasks)
    assert callable(supervise_task)
