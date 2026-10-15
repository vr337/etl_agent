"""M-J Harness 安全执行内核 — 执行租约（防并发重入，T-12.13）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 8 章「高可用 · 副作用执行租约防并发重入」
覆盖 FR：FR-37（执行租约防并发重入）
对应 AC：AC-06/08（受管执行与受管回滚的前置）

S6 边界：**只提供接口与进程内实现**；实际租约获取发生在 S7 Worker（提示词 8.9）。
实现为进程内字典 + TTL：S7 换 DB / Redis 实现时保持 `LeaseManager` 协议不变即可。
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from etl_agent.harness.errors import LeaseHeld

DEFAULT_TTL_SECONDS = 300


@dataclass(frozen=True)
class ExecutionLease:
    """执行租约（同一副作用 + 资源集只能有一个持有者）。"""

    key: str
    owner: str
    acquired_at: datetime
    expires_at: datetime


class LeaseManager:
    """进程内租约管理器（S7 可换 DB/Redis 实现，保持协议一致）。"""

    def __init__(self) -> None:
        self._leases: dict[str, ExecutionLease] = {}

    async def acquire(
        self,
        key: str,
        *,
        owner: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        now: datetime | None = None,
    ) -> ExecutionLease:
        """获取租约；已被他人持有（未过期）→ `HARNESS_409_LEASE_HELD`（FR-37）。

        Args:
            key: 租约键（如 `f"{preparation_id}:{side_effect_type}"`）。
            owner: 持有者标识（执行实例 / 任务 ID）。
            ttl_seconds: 租约时长；过期后可被重新获取。
            now: 当前时间（测试可注入）。

        Raises:
            LeaseHeld: 租约已被占用。
        """
        async with asyncio.Lock():
            moment = now or datetime.now(UTC)
            current = self._leases.get(key)
            if current is not None and current.expires_at > moment and current.owner != owner:
                raise LeaseHeld(f"执行租约已被占用：{key}（FR-37 防并发重入）")
            lease = ExecutionLease(
                key=key,
                owner=owner,
                acquired_at=moment,
                expires_at=moment + timedelta(seconds=ttl_seconds),
            )
            self._leases[key] = lease
            return lease

    async def release(self, key: str, *, owner: str) -> None:
        """释放租约（仅持有者可释放；非持有者静默忽略，避免误释放他人租约）。"""
        async with asyncio.Lock():
            current = self._leases.get(key)
            if current is not None and current.owner == owner:
                del self._leases[key]

    async def get(self, key: str, *, now: datetime | None = None) -> ExecutionLease | None:
        """查询有效租约（过期视为不存在）。"""
        moment = now or datetime.now(UTC)
        current = self._leases.get(key)
        if current is None or current.expires_at <= moment:
            return None
        return current

    @staticmethod
    def build_key(preparation_id: uuid.UUID, side_effect_type: str) -> str:
        """租约键：同一准备单的同类型副作用只允许一个执行（FR-37）。"""
        return f"{preparation_id}:{side_effect_type}"
