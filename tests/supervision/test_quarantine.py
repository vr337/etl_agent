"""QUARANTINE 隔离用例（T-16.11，AC-07/AC-08 联动）。

覆盖：隔离写入时间戳与监督动作、幂等（重复隔离不重复写事件）、
`is_quarantined` 判据、**隔离后质量判定强制 FAILED**、**隔离后原子 Swap 被拒**。
"""
from __future__ import annotations

from sqlalchemy import select

from etl_agent.execution.atomic_swap import LoggingSwapExecutor
from etl_agent.execution.errors import SwapQuarantined
from etl_agent.execution.publish import PublishTargets, evaluate_publish_gate, publish_run
from etl_agent.execution.routing import (
    STATUS_FAILED,
    STATUS_PASSED,
    QualityOutcome,
    classify_quality,
    generate_quality_report,
)
from etl_agent.infrastructure.models import ExecutionEvent
from etl_agent.supervision.mitigation import ACTION_QUARANTINE
from etl_agent.supervision.quarantine import is_quarantined, quarantine_run


async def test_quarantine_writes_timestamp_and_action(db_session, make_run) -> None:
    """隔离写入 `quarantined_at` 与 `supervision_action=QUARANTINE`。"""
    run = await make_run(status="RUNNING")
    await quarantine_run(db_session, run, reason="拒绝率超限")
    assert run.quarantined_at is not None
    assert run.supervision_action == ACTION_QUARANTINE
    assert is_quarantined(run) is True


async def test_quarantine_idempotent_event(db_session, make_run) -> None:
    """重复隔离只写一条事件（避免每轮监督刷一条）。"""
    run = await make_run(status="RUNNING")
    await quarantine_run(db_session, run, reason="第一次")
    await quarantine_run(db_session, run, reason="第二次")
    events = (
        (
            await db_session.execute(
                select(ExecutionEvent).where(
                    ExecutionEvent.run_id == run.id,
                    ExecutionEvent.event_type == "execution.quarantined",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1


async def test_quarantine_forces_quality_failed(db_session, make_run) -> None:
    """隔离后质量判定强制 FAILED（即使总行数与拒绝率都达标）。"""
    contract = {"max_rejected_rows": 100, "max_reject_rate": 0.1}
    healthy = QualityOutcome(total_rows=10_000, passed_rows=10_000, rejected_rows=0)
    # 未隔离 → PASSED
    assert classify_quality(contract, healthy)[0] == STATUS_PASSED
    # 隔离 → FAILED
    assert classify_quality(contract, healthy, quarantined=True)[0] == STATUS_FAILED


async def test_quarantine_blocks_swap(db_session, make_run) -> None:
    """AC-07/AC-08 联动红线：隔离后原子 Swap 被拒（BIZ_422_SWAP_QUARANTINED）。"""
    run = await make_run(status="RUNNING")
    await quarantine_run(db_session, run, reason="拒绝率超限")
    report = await generate_quality_report(
        db_session,
        run.id,
        {"max_rejected_rows": 100, "max_reject_rate": 0.1},
        QualityOutcome(total_rows=10_000, passed_rows=10_000, rejected_rows=0),
        quarantined=True,
    )
    targets = PublishTargets(
        target_table="orders_wide",
        shadow_table="orders_wide_shadow",
        error_table="orders_wide_error",
        quality_contract={},
    )
    decision = await evaluate_publish_gate(
        db_session, run, report=report, targets=targets
    )
    assert decision.allowed is False
    assert decision.code == SwapQuarantined.code

    executor = LoggingSwapExecutor()
    outcome = await publish_run(
        db_session, run, report=report, targets=targets, executor=executor
    )
    assert outcome.published is False
    assert outcome.blocked is True
    assert executor.swapped == []  # 未执行任何 swap


async def test_quarantine_report_marks_failed(db_session, make_run) -> None:
    """隔离下生成的报告状态为 FAILED 且诊断含隔离说明。"""
    run = await make_run(status="RUNNING")
    report = await generate_quality_report(
        db_session,
        run.id,
        {"max_rejected_rows": 100, "max_reject_rate": 0.1},
        QualityOutcome(total_rows=10_000, passed_rows=10_000, rejected_rows=0),
        quarantined=True,
    )
    assert report.status == STATUS_FAILED
    kinds = {item.get("kind") for item in (report.diagnostics or [])}
    assert "quarantined" in kinds
