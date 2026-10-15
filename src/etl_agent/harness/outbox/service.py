"""M-J Harness 安全执行内核 — 事务 Outbox（T-12.11 / T-12.12 状态机）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 5.2 章 harness_command_outbox / 第 8 章「高可用 · Outbox 扫描 2s」
覆盖 FR：FR-35（业务事实与 Worker 命令同事务落库；PENDING/SENDING/SENT/FAILED + 重试 + 锁定）
对应 AC：AC-05（Commit 同事务写 Outbox）

S6 边界：**只提供写入与状态机接口**（PENDING 由 Commit 同事务写入）；
扫描 / 领取 / 发送属 S7 Worker/Beat（禁止事项 24），本模块不启动任何定时任务。
领取使用条件 UPDATE（`status=PENDING AND (locked_until IS NULL OR locked_until < :now)`），
保证多副本下同一命令只被一个扫描器领走。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import HarnessCommandOutbox

STATUS_PENDING = "PENDING"
STATUS_SENDING = "SENDING"
STATUS_SENT = "SENT"
STATUS_FAILED = "FAILED"

ALL_STATUSES: tuple[str, ...] = (STATUS_PENDING, STATUS_SENDING, STATUS_SENT, STATUS_FAILED)

DEFAULT_LOCK_SECONDS = 30


async def enqueue(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    command_type: str,
    payload: dict[str, object],
) -> HarnessCommandOutbox:
    """写入 PENDING 命令（Commit 同事务调用，FR-35）。"""
    row = HarnessCommandOutbox(
        project_id=project_id,
        command_type=command_type,
        payload=payload,
        status=STATUS_PENDING,
        retry_count=0,
    )
    session.add(row)
    await session.flush()
    return row


async def claim_next(
    session: AsyncSession, *, now: datetime | None = None, lock_seconds: int = DEFAULT_LOCK_SECONDS
) -> HarnessCommandOutbox | None:
    """领取一条待发送命令（S7 扫描器入口；S6 仅提供接口）。

    领取条件：PENDING 且未被锁（或锁已过期）；领取后置 SENDING 并加锁。
    返回 None 表示当前无可发送命令。
    """
    moment = now or datetime.now(UTC)
    locked_until = moment + timedelta(seconds=lock_seconds)
    row = (
        (
            await session.execute(
                select(HarnessCommandOutbox)
                .where(
                    HarnessCommandOutbox.status == STATUS_PENDING,
                    (HarnessCommandOutbox.locked_until.is_(None))
                    | (HarnessCommandOutbox.locked_until < moment),
                )
                .order_by(HarnessCommandOutbox.created_at)
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        return None
    result = await session.execute(
        update(HarnessCommandOutbox)
        .where(
            HarnessCommandOutbox.id == row.id,
            HarnessCommandOutbox.status == STATUS_PENDING,
            (HarnessCommandOutbox.locked_until.is_(None))
            | (HarnessCommandOutbox.locked_until < moment),
        )
        .values(status=STATUS_SENDING, locked_until=locked_until)
    )
    affected: int = cast("CursorResult[Any]", result).rowcount or 0
    if affected != 1:
        return None  # 并发领取：已被其他扫描器领走
    await session.refresh(row)
    return row


async def mark_sent(session: AsyncSession, command_id: uuid.UUID) -> HarnessCommandOutbox:
    """标记已发送（S7 投递成功回调）。"""
    row = await session.get(HarnessCommandOutbox, command_id)
    if row is None:
        raise _not_found()
    row.status = STATUS_SENT
    row.locked_until = None
    row.sent_at = datetime.now(UTC)
    await session.flush()
    return row


async def mark_failed(
    session: AsyncSession, command_id: uuid.UUID, error: str, *, now: datetime | None = None
) -> HarnessCommandOutbox:
    """标记失败并递增重试计数（指数退避的锁定窗口由 S7 计算）。"""
    row = await session.get(HarnessCommandOutbox, command_id)
    if row is None:
        raise _not_found()
    row.status = STATUS_FAILED
    row.retry_count += 1
    row.last_error = error
    # 立即释放锁，允许下一轮扫描重试（重试策略与退避在 S7 Worker）
    row.locked_until = now or datetime.now(UTC)
    await session.flush()
    return row


def _not_found() -> LookupError:
    return LookupError("Outbox 命令不存在")
