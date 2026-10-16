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
    # 错误表幂等创建（T-SHADOW-01：脏数据分流目标）
    assert shadow.errors_created == ["orders_wide_error"]


def test_shadow_contract_mismatch_uses_direct_comparison() -> None:
    """T-SHADOW-02：valid_sink 统一后**直接比较**（不再二次拼 `_shadow` 后缀）。

    run23 实证的误报：旧实现对 `valid_sink` 二次拼后缀，把 `order_wide_shadow`
    拼成 `order_wide_shadow_shadow` → 恒判漂移，事件流出现无意义 WARN。
    """
    from etl_agent.worker.tasks.consume_outbox import _shadow_contract_mismatch

    # 统一命名后：契约声明的 valid_sink 就是影子表名本身 → 不漂移
    assert not _shadow_contract_mismatch({"valid_sink": "order_wide_shadow"}, "order_wide_shadow")
    # 真的漂移仍要报（旧值 `_valid`、或指向别的表）
    assert _shadow_contract_mismatch({"valid_sink": "order_wide_valid"}, "order_wide_shadow")
    assert _shadow_contract_mismatch({"valid_sink": "other_shadow"}, "order_wide_shadow")
    # 契约未声明 valid_sink：无从判定，不报漂移
    assert not _shadow_contract_mismatch({}, "order_wide_shadow")


async def test_shadow_executor_factory_is_used_for_data_plane(
    db_session: AsyncSession, seeded_execution: SeededExecution
) -> None:
    """T-SHADOW-01：生产路径经**工厂**按执行视角坐标构造真实 Doris 数据面通道。

    接线前 `scan_outbox_once` 从不传 shadow_executor → 永远回落 Logging 通道，
    影子表只在内存里「记录」，Doris 里根本没有表（T-SHADOW-01 的原始症状）。
    """
    engine = FakeSeaTunnelClient(statuses=["FINISHED"])
    built: list[LoggingShadowTableExecutor] = []

    def _factory(values: dict[str, str]) -> LoggingShadowTableExecutor:
        executor = LoggingShadowTableExecutor()
        built.append(executor)
        return executor

    await scan_outbox_once(
        db_session,
        handoff=seeded_execution.handoff,
        engine=engine,
        settings=make_settings(),
        candidate_loader=_loader(seeded_execution),
        shadow_executor_factory=_factory,
        worker_id="worker-test",
    )

    assert len(built) == 1, "工厂必须被调用（否则数据面通道仍是 Logging 空转）"
    assert built[0].created == ["orders_wide_shadow"]
    assert built[0].errors_created == ["orders_wide_error"]

    run = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert run is not None
    assert run.status == execution_service.STATUS_SUCCEEDED
    assert run.seatunnel_job_id == "seatunnel-job-1"
    assert run.engine_status == "FINISHED"
    assert run.source_rows == 1000 and run.sink_rows == 900

    # 令牌已消费（Replay Guard 事实落库，禁止事项 18）
    grant = await db_session.get(HarnessCapabilityGrant, seeded_execution.token_id)
    assert grant is not None and grant.consumed_at is not None

    # Outbox 已销账
    outbox = await db_session.get(HarnessCommandOutbox, seeded_execution.outbox_id)
    assert outbox is not None and outbox.status == "SENT"

    # 质量报告落库（PASSED：拒绝率 10% 未超阈值，且样本量 >= 最低质量行数 100）
    report = (
        (await db_session.execute(select(QualityReport).where(QualityReport.run_id == run.id)))
        .scalars()
        .first()
    )
    assert report is not None
    assert report.status == STATUS_PASSED
    assert report.total_rows == 1000 and report.rejected_rows == 100
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


# ---------------------------------------------------------------------------
# T-SHADOW-01 PR-B：原子 Swap 接线（AC-16 第二段）
# ---------------------------------------------------------------------------


def _plan_with_contract(base: dict[str, object], **overrides: object) -> dict[str, object]:
    """在种子计划上覆盖质量契约字段（用于构造 blocked / FAILED 场景）。"""
    plan = dict(base)
    contract = dict(plan.get("quality_contract") or {})
    contract.update(overrides)
    plan["quality_contract"] = contract
    return plan


async def _event_of(
    db_session: AsyncSession, run_id: uuid.UUID, event_type: str
) -> ExecutionEvent | None:
    return (
        (
            await db_session.execute(
                select(ExecutionEvent).where(
                    ExecutionEvent.run_id == run_id,
                    ExecutionEvent.event_type == event_type,
                )
            )
        )
        .scalars()
        .first()
    )


async def test_finalize_publishes_atomic_swap_when_quality_passed(
    db_session: AsyncSession, seeded_execution: SeededExecution
) -> None:
    """T-SHADOW-01 PR-B：FINISHED + 质量 PASSED → 真调 `publish_run`（AC-16 第二段）。

    此前 `publish_run` / `resolve_targets` 在 `src/` 下**零调用点**（与 F3 审计钩子同类
    的死代码）——本断言就是接线的回归防线。
    """
    from etl_agent.execution.atomic_swap import LoggingSwapExecutor

    run = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert run is not None
    run.status = execution_service.STATUS_RUNNING
    run.seatunnel_job_id = "job-x"
    await db_session.flush()

    engine = FakeSeaTunnelClient(statuses=["FINISHED"])
    swap = LoggingSwapExecutor()

    await advance_running_executions(
        db_session,
        engine,
        settings=make_settings(),
        candidate_loader=_loader(seeded_execution),
        swap_executor=swap,
    )

    # 1) swap 真被执行（Logging 通道记录 SQL 与表对）
    assert swap.swapped == [("orders_wide", "orders_wide_shadow")]
    assert swap.executed == [
        "ALTER TABLE orders_wide REPLACE WITH TABLE orders_wide_shadow PROPERTIES('swap'='true');"
    ]
    # 2) 事件流含 execution.published（证据链可整流）
    assert await _event_of(db_session, run.id, "execution.published") is not None
    # 3) 终态仍 SUCCEEDED
    refreshed = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert refreshed is not None and refreshed.status == execution_service.STATUS_SUCCEEDED
    # 4) 假消息已改：不再声称「S7 不 Swap」
    quality_event = await _event_of(db_session, run.id, "execution.quality_report")
    assert quality_event is not None
    assert "S7 不 Swap" not in (quality_event.message or "")


async def test_swap_falls_back_to_logging_without_factory(
    db_session: AsyncSession, seeded_execution: SeededExecution
) -> None:
    """无 factory（开发/测试环境）→ 回落 LoggingSwapExecutor，发布链路不中断。"""
    run = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert run is not None
    run.status = execution_service.STATUS_RUNNING
    run.seatunnel_job_id = "job-x"
    await db_session.flush()

    engine = FakeSeaTunnelClient(statuses=["FINISHED"])

    await advance_running_executions(
        db_session,
        engine,
        settings=make_settings(),
        candidate_loader=_loader(seeded_execution),
    )

    assert await _event_of(db_session, run.id, "execution.published") is not None


async def test_blocked_publish_does_not_block_succeeded(
    db_session: AsyncSession, seeded_execution: SeededExecution
) -> None:
    """质量未过 → 发布被阻断，但**不阻塞 SUCCEEDED**（publish.py 既有契约）。

    「不发布」是合法结果；终态原因里写明「发布被阻断」，运维不必翻事件流才知道
    （T-OBS-01 的最小可观测补偿）。
    """
    from etl_agent.execution.atomic_swap import LoggingSwapExecutor

    run = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert run is not None
    run.status = execution_service.STATUS_RUNNING
    run.seatunnel_job_id = "job-x"
    await db_session.flush()

    # Fake 引擎回传 rejected=100；把阈值压到 1 → 质量 FAILED → 门禁②阻断
    plan = _plan_with_contract(seeded_execution.plan, max_rejected_rows=1)

    async def _load(version_id: uuid.UUID) -> dict[str, object] | None:
        return {"etl_plan": plan, "hocon": seeded_execution.hocon}

    engine = FakeSeaTunnelClient(statuses=["FINISHED"])
    swap = LoggingSwapExecutor()

    await advance_running_executions(
        db_session,
        engine,
        settings=make_settings(),
        candidate_loader=_load,
        swap_executor=swap,
    )

    assert swap.swapped == []  # 未发布
    blocked = await _event_of(db_session, run.id, "execution.publish_blocked")
    assert blocked is not None and blocked.level == "WARN"
    refreshed = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert refreshed is not None and refreshed.status == execution_service.STATUS_SUCCEEDED
    succeeded = await _event_of(db_session, run.id, "execution.succeeded")
    assert succeeded is not None
    assert "发布被阻断" in (succeeded.message or "")


async def test_swap_failure_fails_the_run(
    db_session: AsyncSession, seeded_execution: SeededExecution
) -> None:
    """swap 抛异常 → 运行置 FAILED（ENGINE / finalize），并留 publish_failed 事件。"""

    class _FailingSwap:
        async def describe_columns(self, table: str) -> list[object]:
            return []

        async def swap(self, *, target_table: str, shadow_table: str) -> str:
            raise RuntimeError("doris down")

    run = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert run is not None
    run.status = execution_service.STATUS_RUNNING
    run.seatunnel_job_id = "job-x"
    await db_session.flush()

    engine = FakeSeaTunnelClient(statuses=["FINISHED"])

    await advance_running_executions(
        db_session,
        engine,
        settings=make_settings(),
        candidate_loader=_loader(seeded_execution),
        swap_executor=_FailingSwap(),
    )

    refreshed = await db_session.get(ExecutionRun, seeded_execution.run_id)
    assert refreshed is not None
    assert refreshed.status == execution_service.STATUS_FAILED
    assert refreshed.failure_category == "ENGINE"
    assert refreshed.failure_stage == "finalize"
    assert await _event_of(db_session, run.id, "execution.publish_failed") is not None


def test_evaluate_publish_gate_no_longer_takes_executor() -> None:
    """T-SHADOW-01 PR-B：门禁函数的僵尸 `executor` 参数已删（T-PUBLISH-01 记收拢）。"""
    import inspect

    from etl_agent.execution.publish import evaluate_publish_gate

    assert "executor" not in inspect.signature(evaluate_publish_gate).parameters
    assert set(inspect.signature(evaluate_publish_gate).parameters) >= {
        "session",
        "run",
        "report",
        "targets",
    }


async def test_data_plane_ready_message_no_longer_claims_no_swap(
    db_session: AsyncSession, seeded_execution: SeededExecution
) -> None:
    """T-SHADOW-01 PR-B：`shadow_ready` 消息不得再声称「S7 不 Swap」（swap 已真发生）。"""
    engine = FakeSeaTunnelClient(statuses=["FINISHED"])

    await _scan(db_session, seeded_execution, engine)

    event = await _event_of(db_session, seeded_execution.run_id, "execution.shadow_ready")
    assert event is not None
    assert "S7 不 Swap" not in (event.message or "")
    assert "orders_wide_shadow" in (event.message or "")
    assert "orders_wide_error" in (event.message or "")
