"""M-K 执行与数据面 — ExecutionEvent 事件流查询（FR-39）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 5.2 章 execution_events / 第 6.1 章 API-12
覆盖 FR：FR-39（执行事件流：level / stage / event_type）
对应 AC：AC-06（事件流供运行中心查询执行健康度）

只读查询：事件的写入在 `execution.service.record_event`（与状态迁移同事务），
本模块只提供按运行列举的事件序列（按 occurred_at 升序）。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import ExecutionEvent


async def list_events(session: AsyncSession, run_id: uuid.UUID) -> list[ExecutionEvent]:
    """按时间升序列出运行的事件流（API-12 出参，FR-39）。"""
    result = await session.execute(
        select(ExecutionEvent)
        .where(ExecutionEvent.run_id == run_id)
        .order_by(ExecutionEvent.occurred_at, ExecutionEvent.id)
    )
    return list(result.scalars().all())
