"""ExecutionRun 状态机用例（T-13.8 / FR-39）。

覆盖：七态全可达、非法迁移拒绝、终态不可再迁移、每次迁移写事件流并刷新 last_observed_at。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution import service as execution_service
from etl_agent.execution.errors import ExecutionStateConflict
from etl_agent.infrastructure.models import ExecutionEvent, ExecutionRun
from tests.execution.conftest import RunFactory


async def _events(db_session: AsyncSession, run_id: object) -> list[ExecutionEvent]:
    return list(
        (
            await db_session.execute(
                select(ExecutionEvent)
                .where(ExecutionEvent.run_id == run_id)
                .order_by(ExecutionEvent.occurred_at, ExecutionEvent.id)
            )
        )
        .scalars()
        .all()
    )


async def test_state_machine_all_transitions(
    db_session: AsyncSession, make_run: RunFactory
) -> None:
    """七态全可达：QUEUED→SUBMITTED→RUNNING→SUCCEEDED / FAILED / CANCELLING→CANCELLED。"""
    success = await make_run()
    await execution_service.transition(db_session, success, "SUBMITTED")
    await execution_service.transition(db_session, success, "RUNNING")
    await execution_service.transition(db_session, success, "SUCCEEDED")
    assert success.status == "SUCCEEDED"
    assert success.submitted_at and success.started_at and success.finished_at

    failed = await make_run()
    await execution_service.transition(db_session, failed, "SUBMITTED")
    await execution_service.transition(db_session, failed, "RUNNING")
    await execution_service.transition(
        db_session, failed, "FAILED", reason="引擎失败", failure_category="ENGINE"
    )
    assert failed.status == "FAILED"
    assert failed.failure_category == "ENGINE"

    cancelled = await make_run()
    await execution_service.transition(db_session, cancelled, "SUBMITTED")
    await execution_service.transition(db_session, cancelled, "RUNNING")
    await execution_service.transition(db_session, cancelled, "CANCELLING")
    await execution_service.transition(db_session, cancelled, "CANCELLED")
    assert cancelled.status == "CANCELLED"

    # 七态集合完整（SPEC 5.2 注释）
    assert set(execution_service.ALL_STATUSES) == {
        "QUEUED", "SUBMITTED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLING", "CANCELLED",
    }


async def test_illegal_transition_rejected(
    db_session: AsyncSession, make_run: RunFactory
) -> None:
    """跳跃迁移（QUEUED→RUNNING）拒绝，且不留下半迁移现场。"""
    run = await make_run()
    with pytest.raises(ExecutionStateConflict):
        await execution_service.transition(db_session, run, "RUNNING")
    assert run.status == "QUEUED"
    assert await _events(db_session, run.id) == []


async def test_terminal_cannot_transition(
    db_session: AsyncSession, make_run: RunFactory
) -> None:
    """终态不可再迁移（FR-39）。"""
    run = await make_run(status="SUCCEEDED")
    with pytest.raises(ExecutionStateConflict):
        await execution_service.transition(db_session, run, "FAILED")
    assert execution_service.is_terminal("SUCCEEDED")
    assert not execution_service.is_terminal("RUNNING")


async def test_transition_writes_event_and_observes(
    db_session: AsyncSession, make_run: RunFactory
) -> None:
    """每次迁移写事件流并刷新 last_observed_at（FR-39：事件流是运行中心的唯一事实源）。"""
    run = await make_run()
    await execution_service.transition(db_session, run, "SUBMITTED", reason="Worker 已领取")

    events = await _events(db_session, run.id)
    assert len(events) == 1
    assert events[0].event_type == "execution.submitted"
    assert events[0].stage == "submit"
    assert events[0].message == "Worker 已领取"
    assert run.last_observed_at is not None


async def test_stage_of_mapping() -> None:
    """状态到阶段的映射（事件流 stage 字段）。"""
    assert execution_service.stage_of("QUEUED") == "prepare"
    assert execution_service.stage_of("SUBMITTED") == "submit"
    assert execution_service.stage_of("RUNNING") == "run"
    assert execution_service.stage_of("FAILED") == "finalize"
    assert execution_service.stage_of("CANCELLED") == "cancel"


async def test_load_run_by_preparation(
    db_session: AsyncSession, make_run: RunFactory
) -> None:
    """按准备单回溯运行（一准备单一运行，SPEC 5.2 UK）。"""
    run = await make_run()
    found = await execution_service.load_run_by_preparation(
        db_session, run.harness_preparation_id
    )
    assert found is not None and found.id == run.id


async def test_load_run_project_traces_to_project(
    db_session: AsyncSession, make_run: RunFactory, seeded: object
) -> None:
    """运行 → 版本 → 管线 → 项目四级回溯（API-12/14 的鉴权依据）。"""
    run = await make_run()
    loaded, project_id = await execution_service.load_run_project(db_session, run.id)
    assert loaded.id == run.id
    assert project_id == seeded.project_a_id


async def test_load_run_not_found(db_session: AsyncSession) -> None:
    """不存在的运行 → BIZ_404_EXECUTION_NOT_FOUND（不泄露存在性）。"""
    import uuid

    from etl_agent.execution.errors import ExecutionNotFound

    with pytest.raises(ExecutionNotFound):
        await execution_service.load_run(db_session, uuid.uuid4())


async def test_run_status_survives_reload(
    db_session: AsyncSession, make_run: RunFactory
) -> None:
    """状态迁移落库（会话内 flush 后可重新加载到同一事实）。"""
    run = await make_run()
    await execution_service.transition(db_session, run, "SUBMITTED")
    reloaded = await db_session.get(ExecutionRun, run.id)
    assert reloaded is not None and reloaded.status == "SUBMITTED"
