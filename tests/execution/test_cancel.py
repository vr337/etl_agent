"""执行取消用例（T-13.13 / FR-49）。

覆盖：RUNNING/SUBMITTED → CANCELLING → CANCELLED、取消原因留痕、重复申请幂等、
终态 / QUEUED 不可取消、确认取消的前置状态校验。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution.cancel import confirm_cancel, request_cancel
from etl_agent.execution.errors import ExecutionStateConflict
from etl_agent.infrastructure.models import ExecutionEvent
from tests.execution.conftest import RunFactory


async def _event_types(db_session: AsyncSession, run_id: object) -> list[str]:
    return [
        item.event_type
        for item in (
            await db_session.execute(
                select(ExecutionEvent)
                .where(ExecutionEvent.run_id == run_id)
                .order_by(ExecutionEvent.occurred_at, ExecutionEvent.id)
            )
        )
        .scalars()
        .all()
    ]


async def test_cancel_two_phase(db_session: AsyncSession, make_run: RunFactory) -> None:
    """RUNNING → CANCELLING → CANCELLED，取消原因进事件流（FR-49）。"""
    run = await make_run(status="RUNNING")

    status, reason = await request_cancel(db_session, run.id, reason="操作员主动终止")
    assert status == "CANCELLING"
    assert reason == "操作员主动终止"

    await confirm_cancel(db_session, run.id)
    refreshed = await db_session.get(type(run), run.id)
    assert refreshed is not None and refreshed.status == "CANCELLED"
    assert refreshed.finished_at is not None

    types = await _event_types(db_session, run.id)
    assert "execution.cancelling" in types
    assert "execution.cancel_requested" in types
    assert "execution.cancelled" in types


async def test_cancel_from_submitted(db_session: AsyncSession, make_run: RunFactory) -> None:
    """SUBMITTED 也可取消（尚未进入引擎长跑的窗口）。"""
    run = await make_run(status="SUBMITTED")
    status, _ = await request_cancel(db_session, run.id, reason="提交后发现配置错误")
    assert status == "CANCELLING"


async def test_cancel_idempotent_request(db_session: AsyncSession, make_run: RunFactory) -> None:
    """重复申请取消幂等（不重复迁移、不重复写事件）。"""
    run = await make_run(status="RUNNING")
    await request_cancel(db_session, run.id, reason="第一次")
    before = await _event_types(db_session, run.id)

    status, reason = await request_cancel(db_session, run.id, reason="第二次")

    assert status == "CANCELLING"
    assert reason == "第二次"
    assert await _event_types(db_session, run.id) == before


async def test_cancel_rejected_on_terminal(db_session: AsyncSession, make_run: RunFactory) -> None:
    """终态运行不可取消（FR-49）。"""
    run = await make_run(status="SUCCEEDED")
    with pytest.raises(ExecutionStateConflict):
        await request_cancel(db_session, run.id, reason="无效请求")


async def test_cancel_rejected_on_queued(db_session: AsyncSession, make_run: RunFactory) -> None:
    """QUEUED 尚未进入执行，不可取消（取消申请面向已提交/运行中的作业）。"""
    run = await make_run(status="QUEUED")
    with pytest.raises(ExecutionStateConflict):
        await request_cancel(db_session, run.id, reason="无效请求")


async def test_confirm_requires_cancelling(db_session: AsyncSession, make_run: RunFactory) -> None:
    """确认取消要求当前状态为 CANCELLING（防跳跃迁移）。"""
    run = await make_run(status="RUNNING")
    with pytest.raises(ExecutionStateConflict):
        await confirm_cancel(db_session, run.id)


async def test_confirm_cancel_idempotent(db_session: AsyncSession, make_run: RunFactory) -> None:
    """已 CANCELLED 再次确认幂等返回。"""
    run = await make_run(status="CANCELLING")
    await confirm_cancel(db_session, run.id)
    await confirm_cancel(db_session, run.id)
    refreshed = await db_session.get(type(run), run.id)
    assert refreshed is not None and refreshed.status == "CANCELLED"
