"""外部减损动作独立重试用例（T-16.12，FR-46）。

覆盖：5 态（NOT_REQUIRED/PENDING/APPLYING/APPLIED/FAILED）、重试预算、
失败不抛异常（独立状态机，禁止事项 18）、尝试轨迹留痕、失败以状态返回。
"""
from __future__ import annotations

from sqlalchemy import select

from etl_agent.infrastructure.models import ExecutionEvent
from etl_agent.supervision.external_mitigation import (
    ALL_STATUSES,
    DEFAULT_MAX_ATTEMPTS,
    STATUS_APPLIED,
    STATUS_FAILED,
    STATUS_NOT_REQUIRED,
    TERMINAL_STATUSES,
    apply_with_retry,
    not_required,
    record_external_mitigation,
)


def test_five_states_defined() -> None:
    """5 态齐备且顺序与提示词 §8.9 一致。"""
    assert ALL_STATUSES == (
        "NOT_REQUIRED",
        "PENDING",
        "APPLYING",
        "APPLIED",
        "FAILED",
    )
    assert TERMINAL_STATUSES == {"NOT_REQUIRED", "APPLIED", "FAILED"}


def test_default_retry_budget_is_three() -> None:
    """重试预算默认 3 次（提示词 §8.9）。"""
    assert DEFAULT_MAX_ATTEMPTS == 3


async def test_not_required_result() -> None:
    """无外部动作 → NOT_REQUIRED 终态。"""
    result = not_required("STOP_ENGINE_JOB")
    assert result.status == STATUS_NOT_REQUIRED
    assert result.attempts == 0
    assert result.trail == (STATUS_NOT_REQUIRED,)


async def test_apply_success_first_attempt() -> None:
    """首次成功 → APPLIED（轨迹含 PENDING#1 / APPLYING#1 / APPLIED#1）。"""
    calls = {"n": 0}

    async def _ok() -> None:
        calls["n"] += 1

    result = await apply_with_retry("STOP_ENGINE_JOB", _ok)
    assert result.status == STATUS_APPLIED
    assert result.attempts == 1
    assert calls["n"] == 1
    assert result.trail[0] == "PENDING#1"
    assert result.trail[-1] == "APPLIED#1"


async def test_apply_retries_then_succeeds() -> None:
    """前两次失败、第三次成功 → APPLIED，attempts=3。"""
    calls = {"n": 0}

    async def _flaky() -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")

    result = await apply_with_retry("STOP_ENGINE_JOB", _flaky, max_attempts=3)
    assert result.status == STATUS_APPLIED
    assert result.attempts == 3
    assert calls["n"] == 3


async def test_apply_exhausts_budget_to_failed() -> None:
    """耗尽预算 → FAILED（不抛异常，last_error 只留摘要）。"""
    async def _always_fail() -> None:
        raise RuntimeError("engine down " + "x" * 400)

    result = await apply_with_retry("STOP_ENGINE_JOB", _always_fail, max_attempts=2)
    assert result.status == STATUS_FAILED
    assert result.attempts == 2
    assert result.last_error is not None
    assert len(result.last_error) < 200  # 摘要截断（禁止事项 16）
    assert result.failed is True


def test_external_mitigation_retry() -> None:
    """5 态重试（提示词 §9 tests/supervision 清单别名）：状态集与重试预算就位。

    细粒度行为（首试成功 / 重试后成功 / 预算耗尽 → FAILED）由本文件其余用例覆盖。
    """
    assert ALL_STATUSES == ("NOT_REQUIRED", "PENDING", "APPLYING", "APPLIED", "FAILED")
    assert DEFAULT_MAX_ATTEMPTS == 3
    assert TERMINAL_STATUSES == {"NOT_REQUIRED", "APPLIED", "FAILED"}


async def test_apply_never_raises() -> None:
    """外部动作失败**不抛异常**（独立状态机，禁止事项 18）。"""
    async def _boom() -> None:
        raise ValueError("boom")

    result = await apply_with_retry("PAUSE_ENGINE_JOB", _boom, max_attempts=1)
    assert result.status == STATUS_FAILED


async def test_max_attempts_floor_is_one() -> None:
    """非正预算按 1 处理（至少尝试一次）。"""
    calls = {"n": 0}

    async def _ok() -> None:
        calls["n"] += 1

    await apply_with_retry("STOP_ENGINE_JOB", _ok, max_attempts=0)
    assert calls["n"] == 1


async def test_record_external_mitigation_writes_event(db_session, make_run) -> None:
    """外部动作结果写入事件流（独立于快照）。"""
    run = await make_run()
    result = not_required("STOP_ENGINE_JOB")
    await record_external_mitigation(db_session, run.id, result)
    events = (
        (
            await db_session.execute(
                select(ExecutionEvent).where(
                    ExecutionEvent.run_id == run.id,
                    ExecutionEvent.event_type == "execution.external_mitigation",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert "STOP_ENGINE_JOB" in events[0].message
