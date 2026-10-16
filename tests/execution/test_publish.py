"""发布门禁与发布状态机用例（T-15.x，AC-07 核心 / FR-43/44/46）。

覆盖四道门禁（隔离 > 质量 > Schema > 幂等）：
- `test_atomic_swap_success`（AC-07 核心）：质量 PASSED → Swap → 已发布；
- `test_atomic_swap_quality_failed`：质量非 PASSED → 阻断；
- `test_atomic_swap_quarantined`：隔离 → 最高优先阻断（AC-08 联动）；
- `test_atomic_swap_schema_mismatch`：Schema 不一致 → 抛错且不发布；
- `test_atomic_swap_idempotent`：重复触发不二次 swap；
- `test_zero_plaintext_rollback_responses`（AC-02）：响应与事件流零明文。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution.atomic_swap import LoggingSwapExecutor, TableColumn
from etl_agent.execution.errors import SwapQualityFailed, SwapQuarantined, SwapSchemaMismatch
from etl_agent.execution.publish import (
    EVENT_BLOCKED,
    EVENT_PUBLISHED,
    STATE_BLOCKED,
    STATE_PUBLISHED,
    PublishTargets,
    current_publish_state,
    evaluate_publish_gate,
    is_quarantined,
    publish_run,
    resolve_targets,
)
from etl_agent.execution.routing import (
    STATUS_FAILED,
    STATUS_INCOMPLETE,
    STATUS_PASSED,
    STATUS_WARNING,
    QualityOutcome,
    generate_quality_report,
)
from etl_agent.infrastructure.models import ExecutionEvent, ExecutionRun

TARGETS = PublishTargets(
    target_table="orders_wide",
    shadow_table="orders_wide_shadow",
    error_table="orders_wide_error",
    quality_contract={"valid_sink": "orders_wide_shadow"},
)


async def _make_report(session: AsyncSession, run_id, status: str) -> None:
    """造一份指定状态的质量报告（走真实生成路径，避免手拼字段）。"""
    contract = {"max_rejected_rows": 100, "max_reject_rate": 0.1}
    total = 1000
    if status == STATUS_PASSED:
        outcome = QualityOutcome(total_rows=total, passed_rows=1000, rejected_rows=0)
    elif status == STATUS_WARNING:
        outcome = QualityOutcome(total_rows=total, passed_rows=950, rejected_rows=80)
    elif status == STATUS_FAILED:
        outcome = QualityOutcome(total_rows=total, passed_rows=800, rejected_rows=200)
    else:  # INCOMPLETE
        outcome = QualityOutcome(total_rows=None, passed_rows=0, rejected_rows=0)
    await generate_quality_report(session, run_id, contract, outcome)


async def test_atomic_swap_success(db_session: AsyncSession, make_run) -> None:
    """**AC-07 核心**：质量 PASSED → 原子 Swap → 正式表发布成功。"""
    run = await make_run(status="RUNNING")
    await _make_report(db_session, run.id, STATUS_PASSED)
    report = await _report_of(db_session, run.id)

    executor = LoggingSwapExecutor()
    outcome = await publish_run(db_session, run, report=report, targets=TARGETS, executor=executor)
    assert outcome.published is True
    assert outcome.blocked is False
    assert outcome.state == STATE_PUBLISHED
    assert executor.swapped == [("orders_wide", "orders_wide_shadow")]
    assert await current_publish_state(db_session, run.id) == STATE_PUBLISHED

    events = await _events(db_session, run.id)
    assert any(e.event_type == EVENT_PUBLISHED for e in events)


async def test_atomic_swap_quality_failed(db_session: AsyncSession, make_run) -> None:
    """质量报告非 PASSED（FAILED/WARNING/INCOMPLETE）→ 阻断，不 swap（FR-44）。"""
    for status in (STATUS_FAILED, STATUS_WARNING, STATUS_INCOMPLETE):
        run = await make_run(status="RUNNING")
        await _make_report(db_session, run.id, status)
        report = await _report_of(db_session, run.id)
        executor = LoggingSwapExecutor()
        outcome = await publish_run(
            db_session, run, report=report, targets=TARGETS, executor=executor
        )
        assert outcome.published is False, status
        assert outcome.blocked is True, status
        assert outcome.code == SwapQualityFailed.code, status
        assert executor.swapped == [], status


async def test_atomic_swap_quality_missing(db_session: AsyncSession, make_run) -> None:
    """无质量报告 → 阻断（MISSING 按不放行处理）。"""
    run = await make_run(status="RUNNING")
    executor = LoggingSwapExecutor()
    outcome = await publish_run(db_session, run, report=None, targets=TARGETS, executor=executor)
    assert outcome.blocked is True
    assert outcome.code == SwapQualityFailed.code


async def test_atomic_swap_quarantined(db_session: AsyncSession, make_run) -> None:
    """隔离是最高优先阻断：即使质量 PASSED 也禁止发布（AC-08 联动红线）。"""
    run = await make_run(status="RUNNING")
    await _make_report(db_session, run.id, STATUS_PASSED)
    report = await _report_of(db_session, run.id)
    run.supervision_action = "QUARANTINE"
    await db_session.flush()

    assert is_quarantined(run) is True
    executor = LoggingSwapExecutor()
    decision = await evaluate_publish_gate(db_session, run, report=report, targets=TARGETS)
    assert decision.allowed is False
    assert decision.code == SwapQuarantined.code

    outcome = await publish_run(db_session, run, report=report, targets=TARGETS, executor=executor)
    assert outcome.blocked is True
    assert outcome.code == SwapQuarantined.code
    assert executor.swapped == []


async def test_quarantined_at_timestamp_also_blocks(db_session: AsyncSession, make_run) -> None:
    """隔离态也可由 `quarantined_at` 时间戳表达（两种载体任一命中即隔离）。"""
    from datetime import UTC, datetime

    run = await make_run(status="RUNNING")
    run.quarantined_at = datetime.now(UTC)
    await db_session.flush()
    assert is_quarantined(run) is True


async def test_atomic_swap_schema_mismatch(db_session: AsyncSession, make_run) -> None:
    """Schema 不一致 → 抛 SwapSchemaMismatch，不发布（宁可失败也不改表结构）。"""
    run = await make_run(status="RUNNING")
    await _make_report(db_session, run.id, STATUS_PASSED)
    report = await _report_of(db_session, run.id)
    executor = LoggingSwapExecutor(
        schemas={
            "orders_wide": [TableColumn("id", "bigint")],
            "orders_wide_shadow": [TableColumn("id", "string")],
        }
    )
    try:
        await publish_run(db_session, run, report=report, targets=TARGETS, executor=executor)
        raise AssertionError("Schema 不一致，应拒绝发布")
    except SwapSchemaMismatch:
        pass
    assert executor.swapped == []
    events = await _events(db_session, run.id)
    assert any(e.event_type == "execution.publish_failed" for e in events)


async def test_atomic_swap_idempotent(db_session: AsyncSession, make_run) -> None:
    """重复触发发布 → 幂等返回已发布，不二次 swap。"""
    run = await make_run(status="RUNNING")
    await _make_report(db_session, run.id, STATUS_PASSED)
    report = await _report_of(db_session, run.id)
    executor = LoggingSwapExecutor()

    first = await publish_run(db_session, run, report=report, targets=TARGETS, executor=executor)
    second = await publish_run(db_session, run, report=report, targets=TARGETS, executor=executor)

    assert first.published is True and second.published is True
    assert len(executor.swapped) == 1  # 只 swap 一次


async def test_blocked_state_recorded(db_session: AsyncSession, make_run) -> None:
    """被阻断 → 事件流记 publish_blocked，派生状态 BLOCKED。"""
    run = await make_run(status="RUNNING")
    executor = LoggingSwapExecutor()
    await publish_run(db_session, run, report=None, targets=TARGETS, executor=executor)
    assert await current_publish_state(db_session, run.id) == STATE_BLOCKED
    events = await _events(db_session, run.id)
    assert any(e.event_type == EVENT_BLOCKED for e in events)


def test_resolve_targets_from_plan() -> None:
    """从 ETL 计划解析发布目标；无 sink.table 即无发布动作。"""
    plan = {
        "sink": {"table": "orders_wide"},
        "quality_contract": {"valid_sink": "orders_wide_shadow", "error_sink": "orders_wide_error"},
    }
    targets = resolve_targets(plan)
    assert targets is not None
    assert targets.target_table == "orders_wide"
    assert targets.shadow_table == "orders_wide_shadow"
    assert targets.error_table == "orders_wide_error"
    assert resolve_targets({"sink": {}}) is None
    assert resolve_targets(None) is None


def test_resolve_targets_defaults_shadow_suffix() -> None:
    """契约未声明 valid_sink 时以 `<target>_shadow` 兜底。"""
    targets = resolve_targets({"sink": {"table": "t"}})
    assert targets is not None
    assert targets.shadow_table == "t_shadow"


async def test_zero_plaintext_rollback_responses(db_session: AsyncSession, make_run) -> None:
    """AC-02：发布链路事件流消息零明文（无口令 / 无连接串 / 无占位符）。"""
    run = await make_run(status="RUNNING")
    await _make_report(db_session, run.id, STATUS_PASSED)
    report = await _report_of(db_session, run.id)
    executor = LoggingSwapExecutor()
    await publish_run(db_session, run, report=report, targets=TARGETS, executor=executor)

    events = await _events(db_session, run.id)
    for event in events:
        text = event.message.lower()
        assert "password" not in text
        assert "${" not in text
        assert "jdbc://" not in text
    # 已发布事件只含表名与 swap 语义
    published = [e for e in events if e.event_type == EVENT_PUBLISHED]
    assert published
    assert "orders_wide" in published[0].message


async def _report_of(session: AsyncSession, run_id):
    from etl_agent.infrastructure.models import QualityReport

    return (
        (await session.execute(select(QualityReport).where(QualityReport.run_id == run_id)))
        .scalars()
        .one()
    )


async def _events(session: AsyncSession, run_id) -> list[ExecutionEvent]:
    return (
        (await session.execute(select(ExecutionEvent).where(ExecutionEvent.run_id == run_id)))
        .scalars()
        .all()
    )


_ = ExecutionRun
