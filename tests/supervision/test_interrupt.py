"""硬中断用例（T-16.10，AC-08 核心）。

覆盖：RUNNING → CANCELLING → CANCELLED 两段式迁移、SeaTunnel stop-job 被调用、
失败分类固定 CANCELLATION、根因诊断落库、终态不可逆、外部动作失败不阻塞落终态。
"""
from __future__ import annotations

from sqlalchemy import select

from etl_agent.infrastructure.models import ExecutionEvent, ExecutionRun
from etl_agent.supervision.interrupt import (
    EXTERNAL_ACTION_STOP_JOB,
    FAILURE_CATEGORY_CANCELLATION,
    hard_interrupt,
)


async def test_hard_interrupt_running_to_cancelled(db_session, make_run) -> None:
    """AC-08 核心：RUNNING → CANCELLING → CANCELLED，终态 + 诊断落库。"""
    run = await make_run(status="RUNNING", seatunnel_job_id="job-1")

    class _Engine:
        def __init__(self) -> None:
            self.stopped: list[str] = []

        async def stop_job(self, job_id: str) -> None:
            self.stopped.append(job_id)

    engine = _Engine()
    outcome = await hard_interrupt(db_session, run, engine=engine, reason="记录数超预算")

    assert outcome.interrupted is True
    assert run.status == "CANCELLED"
    assert engine.stopped == ["job-1"]
    assert run.failure_category == FAILURE_CATEGORY_CANCELLATION
    assert run.retryable is False
    assert run.diagnosis  # 可解释建议已落
    # 两段式迁移的事件流留痕
    types = (
        (
            await db_session.execute(
                select(ExecutionEvent.event_type).where(ExecutionEvent.run_id == run.id)
            )
        )
        .scalars()
        .all()
    )
    assert "execution.diagnosis" in types


async def test_hard_interrupt_idempotent_on_terminal(db_session, make_run) -> None:
    """终态再中断幂等跳过（CANCELLED 不可逆）。"""
    run = await make_run(status="CANCELLED")
    outcome = await hard_interrupt(db_session, run, engine=None, reason="重复中断")
    assert outcome.interrupted is False
    assert run.status == "CANCELLED"


async def test_hard_interrupt_without_engine_still_settles(db_session, make_run) -> None:
    """无引擎 / 无作业号：外部动作记 NOT_REQUIRED，仍落终态与诊断。"""
    run = await make_run(status="RUNNING")  # 无 seatunnel_job_id
    outcome = await hard_interrupt(db_session, run, engine=None, reason="监督判定")
    assert outcome.interrupted is True
    assert run.status == "CANCELLED"
    assert outcome.external is not None
    assert outcome.external.status == "NOT_REQUIRED"
    assert outcome.external.kind == EXTERNAL_ACTION_STOP_JOB


async def test_hard_interrupt_engine_failure_does_not_block(db_session, make_run) -> None:
    """外部动作失败不阻塞：stop-job 抛错 → 仍落 CANCELLED（禁止事项 18）。"""
    run = await make_run(status="RUNNING", seatunnel_job_id="job-1")

    class _Broken:
        async def stop_job(self, job_id: str) -> None:
            raise RuntimeError("engine down")

    outcome = await hard_interrupt(db_session, run, engine=_Broken(), reason="监督判定")
    assert outcome.interrupted is True
    assert run.status == "CANCELLED"
    assert outcome.external is not None
    assert outcome.external.status == "FAILED"


async def test_hard_interrupt_submitted_state(db_session, make_run) -> None:
    """SUBMITTED 也可硬中断（S7 补入白名单：进入 SUBMITTED 即引擎已提交）。"""
    run = await make_run(status="SUBMITTED", seatunnel_job_id="job-2")
    outcome = await hard_interrupt(db_session, run, engine=None, reason="监督判定")
    assert outcome.interrupted is True
    assert run.status == "CANCELLED"


async def test_hard_interrupt_failure_category_is_cancellation(db_session, make_run) -> None:
    """失败分类固定 CANCELLATION（诊断域 9 类之一，可解释）。"""
    run = await make_run(status="RUNNING")
    await hard_interrupt(db_session, run, engine=None, reason="记录数超预算")
    refreshed = await db_session.get(ExecutionRun, run.id)
    assert refreshed is not None
    assert refreshed.failure_category == "CANCELLATION"
    assert refreshed.failure_stage == "cancel"
