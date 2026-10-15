"""AC-06 核心用例：Worker 消费 Outbox（验签 → 消费令牌 → 物化 → 提交引擎 → 质量报告）。

覆盖（FR-33/34/35/38/39/42/43/44）：单次令牌消费、重放拒绝、签名篡改拒绝、
Secret 物化、引擎提交与推进、终态落质量报告、失败分类落账。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution import service as execution_service
from etl_agent.execution.routing import STATUS_FAILED, STATUS_PASSED
from etl_agent.execution.shadow import LoggingShadowTableExecutor, shadow_table_name
from etl_agent.infrastructure.models import (
    ExecutionEvent,
    ExecutionRun,
    HarnessCapabilityGrant,
    HarnessCommandOutbox,
    QualityReport,
)
from etl_agent.worker.tasks.consume_outbox import advance_running_executions, scan_outbox_once
from tests.helpers import FakeSeaTunnelClient, SeededExecution, make_settings


def _loader(execution: SeededExecution):
    """候选正文加载器替身（真实实现回 Checkpoint 读 `candidate` 通道）。"""

    async def _load(version_id: uuid.UUID) -> dict[str, object] | None:
        assert version_id == execution.version_id
        return {"etl_plan": execution.plan, "hocon": execution.hocon}

    return _load


async def _scan(
    db_session: AsyncSession,
    execution: SeededExecution,
    engine: FakeSeaTunnelClient,
    *,
    shadow_executor: LoggingShadowTableExecutor | None = None,
) -> object:
    return await scan_outbox_once(
        db_session,
        handoff=execution.handoff,
        engine=engine,
        settings=make_settings(),
        candidate_loader=_loader(execution),
        shadow_executor=shadow_executor or LoggingShadowTableExecutor(),
        worker_id="worker-test",
    )


async def test_consume_outbox_happy_path(
    db_session: AsyncSession, seeded_execution: SeededExecution
) -> None:
    """AC-06 核心：Outbox → 验签 → 消费令牌 → 物化 → 提交（mock）→ 终态 SUCCEEDED。"""
    engine = FakeSeaTunnelClient(statuses=["FINISHED"])
    shadow = LoggingShadowTableExecutor()

    summary = await _scan(db_session, seeded_execution, engine, shadow_executor=shadow)

    assert summary.claimed == 1
    assert summary.submitted == 1
    assert summary.finalized == 1
    assert summary.failed == 0

    # 引擎确实被调用（提交正文为物化后的 HOCON；此处只断言「已提交」与占位符已消失）
    assert len(engine.submitted) == 1
    assert "${" not in engine.submitted[0]
    assert len(engine.checked) == 1

    # 影子表预复制（S7 只创建不 Swap，FR-43）
    assert shadow.created == [shadow_table_name("orders_wide")]

    run = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert run is not None
    assert run.status == execution_service.STATUS_SUCCEEDED
    assert run.seatunnel_job_id == "seatunnel-job-1"
    assert run.engine_status == "FINISHED"
    assert run.source_rows == 10 and run.sink_rows == 9

    # 令牌已消费（Replay Guard 事实落库，禁止事项 18）
    grant = await db_session.get(HarnessCapabilityGrant, seeded_execution.token_id)
    assert grant is not None and grant.consumed_at is not None

    # Outbox 已销账
    outbox = await db_session.get(HarnessCommandOutbox, seeded_execution.outbox_id)
    assert outbox is not None and outbox.status == "SENT"

    # 质量报告落库（PASSED：拒绝率 10% 未超阈值）
    report = (
        (await db_session.execute(select(QualityReport).where(QualityReport.run_id == run.id)))
        .scalars()
        .first()
    )
    assert report is not None
    assert report.status == STATUS_PASSED
    assert report.total_rows == 10 and report.rejected_rows == 1
    assert report.valid_sink == "orders_wide_shadow"
    assert report.error_sink == "orders_wide_error"

    # 状态机迁移都写了事件流（FR-39）
    events = (
        (
            await db_session.execute(
                select(ExecutionEvent).where(ExecutionEvent.run_id == run.id)
            )
        )
        .scalars()
        .all()
    )
    event_types = {item.event_type for item in events}
    assert "execution.submitted" in event_types
    assert "execution.running" in event_types
    assert "execution.succeeded" in event_types
    assert "execution.quality_report" in event_types


async def test_consume_outbox_replay_rejected(
    db_session: AsyncSession, seeded_execution: SeededExecution
) -> None:
    """令牌二次消费 → `HARNESS_409_CAPABILITY_REPLAYED`（FR-34 红线）。"""
    engine = FakeSeaTunnelClient()
    await _scan(db_session, seeded_execution, engine)

    # 模拟重复投递：命令重新置 PENDING，且令牌本体**再次出现在交接通道**
    # （只重置命令而不重发令牌，命中的会是「交接缺失」而非重放守卫——两条防线要分别验证）
    outbox = await db_session.get(HarnessCommandOutbox, seeded_execution.outbox_id)
    assert outbox is not None
    outbox.status = "PENDING"
    outbox.locked_until = None
    await db_session.flush()
    await seeded_execution.handoff.publish(
        seeded_execution.token_id, seeded_execution.token, 300
    )

    second = await _scan(db_session, seeded_execution, engine)

    assert second.claimed == 1
    assert second.failed == 1
    assert any("CapabilityReplayed" in item for item in second.details)

    refreshed = await db_session.get(HarnessCommandOutbox, seeded_execution.outbox_id)
    assert refreshed is not None
    assert refreshed.status == "FAILED"
    assert refreshed.last_error is not None and "CapabilityReplayed" in refreshed.last_error


async def test_consume_outbox_signature_invalid(
    db_session: AsyncSession, seeded_execution: SeededExecution
) -> None:
    """签名篡改 → `HARNESS_401_CAPABILITY_INVALID`（不执行任何副作用）。"""
    engine = FakeSeaTunnelClient()
    tampered = seeded_execution.token[:-8] + "0" * 8
    await seeded_execution.handoff.publish(seeded_execution.token_id, tampered, 300)

    summary = await _scan(db_session, seeded_execution, engine)

    assert summary.failed == 1
    assert any("CapabilityInvalid" in item for item in summary.details)
    assert engine.submitted == []  # 验签失败即不提交（失败关闭）

    run = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert run is not None
    assert run.status == execution_service.STATUS_FAILED
    assert run.retryable is False  # 授权类失败不可重试（重试不会让坏令牌变可信）


async def test_consume_outbox_handoff_missing_is_retryable(
    db_session: AsyncSession, seeded_execution: SeededExecution
) -> None:
    """交接通道无令牌 → 失败且**可重试**（不降级为无令牌执行，FR-34）。"""
    engine = FakeSeaTunnelClient()
    # 先取走令牌：模拟 Redis TTL 到期 / 被其他副本取走
    await seeded_execution.handoff.fetch(seeded_execution.token_id)

    summary = await _scan(db_session, seeded_execution, engine)

    assert summary.failed == 1
    assert engine.submitted == []
    run = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert run is not None and run.retryable is True


async def test_engine_failure_marks_run_failed(
    db_session: AsyncSession, seeded_execution: SeededExecution
) -> None:
    """引擎作业失败 → 运行 FAILED + 失败分类 ENGINE / 阶段 run（FR-47 子集）。"""
    engine = FakeSeaTunnelClient(statuses=["FAILED"])

    summary = await _scan(db_session, seeded_execution, engine)

    assert summary.submitted == 1
    assert summary.finalized == 1
    run = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert run is not None
    assert run.status == execution_service.STATUS_FAILED
    assert run.failure_category == "ENGINE"
    assert run.failure_stage == "run"
    assert run.retryable is True


async def test_quality_report_failed_when_threshold_exceeded(
    db_session: AsyncSession, seeded_execution: SeededExecution
) -> None:
    """拒绝行数超契约阈值 → QualityReport FAILED，但运行仍 SUCCEEDED（S7 不做监督）。"""
    engine = FakeSeaTunnelClient(statuses=["FINISHED"])
    engine.metrics = {
        "source_rows": 100,
        "sink_rows": 10,
        "quality": {"total_rows": 100, "passed_rows": 10, "rejected_rows": 90,
                    "error_codes": {"NULL_ID": 90}},
    }
    seeded_execution.plan["quality_contract"] = {
        "valid_sink": "orders_wide_shadow",
        "error_sink": "orders_wide_error",
        "max_reject_rate": 0.5,
        "predicates": [{"name": "not_null_id", "expression": "id IS NOT NULL"}],
    }

    await _scan(db_session, seeded_execution, engine)

    run = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert run is not None and run.status == execution_service.STATUS_SUCCEEDED
    report = (
        (await db_session.execute(select(QualityReport).where(QualityReport.run_id == run.id)))
        .scalars()
        .first()
    )
    assert report is not None and report.status == STATUS_FAILED


async def test_advance_skips_when_engine_unavailable(
    db_session: AsyncSession, seeded_execution: SeededExecution
) -> None:
    """引擎不可达：推进失败但不改状态（下遍重试，不把瞬时故障写成终态）。"""
    from etl_agent.execution.errors import SeatunnelUnavailable

    class _Unavailable:
        async def submit_job(self, hocon: str) -> str:
            raise SeatunnelUnavailable("engine down")

        async def job_status(self, job_id: str) -> object:
            raise SeatunnelUnavailable("engine down")

        async def stop_job(self, job_id: str) -> None:
            raise SeatunnelUnavailable("engine down")

        async def check(self, hocon: str) -> dict[str, object]:
            raise SeatunnelUnavailable("engine down")

    # 直接构造 RUNNING 运行（跳过提交阶段）
    run = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert run is not None
    run.status = execution_service.STATUS_RUNNING
    run.seatunnel_job_id = "job-x"
    await db_session.flush()

    result = await advance_running_executions(
        db_session, _Unavailable(), settings=make_settings()
    )

    assert result.failed == 1
    refreshed = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert refreshed is not None
    assert refreshed.status == execution_service.STATUS_RUNNING


async def test_stalled_run_records_warn_once(
    db_session: AsyncSession, seeded_execution: SeededExecution
) -> None:
    """停滞超阈值 → 记一条 WARN 事件（同一停滞期不重复刷）。"""
    from datetime import UTC, datetime, timedelta

    run = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert run is not None
    run.status = execution_service.STATUS_RUNNING
    run.seatunnel_job_id = "job-x"
    run.started_at = datetime.now(UTC) - timedelta(hours=1)
    await db_session.flush()

    engine = FakeSeaTunnelClient(statuses=["RUNNING", "RUNNING"])
    settings = make_settings(execution_poll_max_wait_seconds=1)

    await advance_running_executions(db_session, engine, settings=settings)
    await advance_running_executions(db_session, engine, settings=settings)

    events = (
        (
            await db_session.execute(
                select(ExecutionEvent).where(
                    ExecutionEvent.run_id == run.id,
                    ExecutionEvent.event_type == "execution.stalled",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].level == "WARN"
