"""M-J Harness 安全执行内核 — Replay Guard（令牌仅消费一次，FR-34）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 5.2 章 harness_capability_grants.consumed_at / 第 8 章「安全 · 防重放与授权」
覆盖 FR：FR-34（Replay Guard 保证令牌仅消费一次）
对应 AC：AC-05（第二次使用同一令牌被拒绝）

原子性：消费用条件 UPDATE（`WHERE consumed_at IS NULL`），rowcount=0 即已被消费——
不是「先读后写」，并发二次消费只有一个事务能成功（DB 判据，非进程内判据）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.harness.errors import (
    CapabilityReplayed,
)
from etl_agent.infrastructure.models import HarnessCapabilityGrant


@dataclass(frozen=True)
class ConsumedGrant:
    """已消费的令牌（Tool Broker 的下发依据）。"""

    token_id: uuid.UUID
    preparation_id: uuid.UUID
    claims: dict[str, object]
    expires_at: datetime


class ReplayGuard:
    """令牌一次性消费守卫（SPEC 8：全局仅消费 1 次）。"""

    async def consume(
        self,
        session: AsyncSession,
        *,
        token_id: uuid.UUID,
        token_sha256: str,
        now: datetime | None = None,
    ) -> ConsumedGrant:
        """标记令牌已消费并返回其 claims（第二次消费直接拒绝）。

        Raises:
            PreparationNotFound:    令牌不存在（复用 `BIZ_404_NOT_FOUND`）。
            CapabilityReplayed:     令牌已被消费（含 token_sha256 不匹配——库中只有摘要）。
        """
        grant = (
            (
                await session.execute(
                    select(HarnessCapabilityGrant).where(
                        HarnessCapabilityGrant.token_id == token_id
                    )
                )
            )
            .scalars()
            .first()
        )
        if grant is None or grant.token_sha256 != token_sha256:
            # 摘要不匹配 = 令牌与登记事实不一致，等同不可信令牌（复用重放语义拒绝）
            raise CapabilityReplayed("Capability 与登记令牌不一致（token_sha256 不匹配）")

        if grant.consumed_at is not None:
            raise CapabilityReplayed("Capability 已被消费：令牌仅可消费一次（FR-34）")

        result = await session.execute(
            update(HarnessCapabilityGrant)
            .where(HarnessCapabilityGrant.token_id == token_id, HarnessCapabilityGrant.consumed_at.is_(None))
            .values(consumed_at=now or datetime.now(UTC))
        )
        affected: int = cast("CursorResult[Any]", result).rowcount or 0
        if affected != 1:
            # 并发消费：另一个事务已抢先消费（DB 判据，防进程内竞态）
            raise CapabilityReplayed("Capability 已被并发消费（FR-34）")
        return ConsumedGrant(
            token_id=grant.token_id,
            preparation_id=grant.preparation_id,
            claims=dict(grant.claims or {}),
            expires_at=grant.expires_at,
        )
