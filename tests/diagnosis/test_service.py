"""诊断服务用例（T-16.13，AC-08 核心）。

覆盖：诊断三件套落库（failure_category / failure_stage / retryable）、
修复建议可解释、**诊断不含完整错误栈正文**、`diagnose_run` 复核补全。
"""
from __future__ import annotations

from sqlalchemy import select

from etl_agent.diagnosis.service import (
    MESSAGE_LIMIT,
    apply_diagnosis,
    build_diagnosis,
    diagnose_run,
    strip_stack,
)
from etl_agent.infrastructure.models import ExecutionRun


def test_9_classifiers_reachability_via_build() -> None:
    """`build_diagnosis` 对显式分类直通（分类 / 可重试 / 建议齐备）。"""
    diagnosis = build_diagnosis(failure_category="ENGINE", failure_stage="run")
    assert diagnosis.failure_category == "ENGINE"
    assert diagnosis.failure_stage == "run"
    assert diagnosis.retryable is True
    assert diagnosis.suggestion


def test_strip_stack_removes_traceback() -> None:
    """栈正文特征行被剔除（File / Traceback / Caused by / at ...）。"""
    messy = (
        "引擎提交失败\n"
        "Traceback (most recent call last):\n"
        '  File "/app/src/engine.py", line 42, in submit\n'
        "    raise RuntimeError('boom')\n"
        "Caused by: java.lang.NullPointerException\n"
        "    at com.foo.Bar.baz(Bar.java:10)\n"
        "    ... 3 more\n"
    )
    cleaned = strip_stack(messy)
    assert "Traceback" not in cleaned
    assert 'File "' not in cleaned
    assert "Caused by" not in cleaned
    assert "... 3 more" not in cleaned
    assert "引擎提交失败" in cleaned


def test_strip_stack_truncates() -> None:
    """超长摘要被截断（只留治理事实）。"""
    cleaned = strip_stack("x" * (MESSAGE_LIMIT + 500))
    assert len(cleaned) == MESSAGE_LIMIT


async def test_diagnosis_no_full_stack(db_session, make_run) -> None:
    """AC-08 红线：诊断落库不含完整错误栈正文。"""
    run = await make_run(status="RUNNING", seatunnel_job_id="job-1")
    stack = (
        "NullPointerException: engine internal error\n"
        "  File \"/opt/seatunnel/lib/app.jar\", line 100\n"
        "    at org.apache.seatunnel.Engine.run(Engine.java:55)\n"
        "Caused by: java.lang.IllegalStateException\n"
    )
    await apply_diagnosis(db_session, run, failure_category="ENGINE", message=stack)
    refreshed = await db_session.get(ExecutionRun, run.id)
    assert refreshed is not None
    assert refreshed.diagnosis is not None
    assert "File \"" not in refreshed.diagnosis
    assert "at org.apache" not in refreshed.diagnosis
    assert "Caused by" not in refreshed.diagnosis
    # 分类与可重试性落库
    assert refreshed.failure_category == "ENGINE"
    assert refreshed.retryable is True


async def test_apply_diagnosis_sets_three_fields(db_session, make_run) -> None:
    """诊断三件套落 `execution_runs`（不新增列，禁止事项 7）。"""
    run = await make_run(status="RUNNING")
    await apply_diagnosis(
        db_session, run, failure_category="CANCELLATION", failure_stage="cancel", message="超预算"
    )
    assert run.failure_category == "CANCELLATION"
    assert run.failure_stage == "cancel"
    assert run.retryable is False
    assert "监督硬中断" in (run.diagnosis or "") or "预算" in (run.diagnosis or "")


async def test_diagnose_run_completes_from_existing_facts(db_session, make_run) -> None:
    """`diagnose_run` 按运行已有事实补全（不重写已有分类）。"""
    run = await make_run(status="FAILED", failure_category="CONNECTIVITY")
    diagnosis = await diagnose_run(db_session, run, message="connection refused")
    assert diagnosis.failure_category == "CONNECTIVITY"
    assert run.failure_category == "CONNECTIVITY"
    assert run.retryable is True


async def test_diagnosis_writes_event(db_session, make_run) -> None:
    """诊断写入事件流（运行中心时间线来源）。"""
    from etl_agent.infrastructure.models import ExecutionEvent

    run = await make_run(status="RUNNING")
    await apply_diagnosis(db_session, run, failure_category="ENGINE", message="engine boom")
    events = (
        (
            await db_session.execute(
                select(ExecutionEvent).where(
                    ExecutionEvent.run_id == run.id,
                    ExecutionEvent.event_type == "execution.diagnosis",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1


async def test_hard_interrupt_diagnosis_is_explainable(db_session, make_run) -> None:
    """硬中断后的诊断可解释（CANCELLATION + 修复建议）。"""
    from etl_agent.supervision.interrupt import hard_interrupt

    run = await make_run(status="RUNNING")
    await hard_interrupt(db_session, run, engine=None, reason="拒绝率超限")
    refreshed = await db_session.get(ExecutionRun, run.id)
    assert refreshed is not None
    assert refreshed.failure_category == "CANCELLATION"
    assert refreshed.diagnosis
    assert "预算" in refreshed.diagnosis or "重跑" in refreshed.diagnosis
