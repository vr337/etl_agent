"""M-J Harness 安全执行内核 — Capability 令牌交接通道（Worker 通道，S7 落地）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 5.2 章
harness_capability_grants（只存 token_sha256）/ 第 8 章「安全 · 防重放与授权」
覆盖 FR：FR-33（令牌本体只在返回值出现一次，转交 Worker 通道）、FR-34（Replay Guard）
对应 AC：AC-05（单次令牌交接不落业务库）、AC-02（令牌本体不落库 / 不进日志）

S6 收尾时的未决问题：Commit 返回令牌本体一次，但**未定义交接通道的实现**
（S6 阶段报告「AMEND-005 草稿」之外的口径空白）。S7 落地为 Redis 单次读通道：

1. API-10 Commit 成功后把令牌本体写入 Redis（键 `harness:capability:{token_id}`，
   TTL = Capability TTL，与令牌过期对齐）；
2. Worker 拉取 Outbox 后经 `fetch` **原子取出**（GETDEL：只可能被取走一次）；
3. 交接通道不可用 → 拒绝执行（`DependencyUnavailable`，503）——
   **不降级为无令牌执行**（那等于绕过 Replay Guard，FR-34 红线）。

为什么不放进 Outbox payload：payload 落业务库（JSON 列），令牌本体是 bearer
凭据，入库即违反「库中只存 token_sha256」（SPEC 5.2 / FR-33）。
Redis 单次读（GETDEL）+ TTL 与令牌同步过期 + 消费后即失效三重约束下，
交接通道不存在「二次交付同一令牌」的窗口。
"""
from __future__ import annotations

import uuid
from typing import Protocol, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from etl_agent.execution.errors import DependencyUnavailable


class CapabilityHandoff(Protocol):
    """令牌交接通道协议（API-10 写入，Worker 原子取出）。"""

    async def publish(self, token_id: uuid.UUID, token: str, ttl_seconds: int) -> None: ...

    async def fetch(self, token_id: uuid.UUID) -> str | None: ...


class RedisCapabilityHandoff:
    """Redis 交接通道（生产通道；GETDEL 保证单次交付）。"""

    def __init__(self, client: Redis, *, key_prefix: str = "harness:capability") -> None:
        self._client = client
        self._key_prefix = key_prefix

    def _key(self, token_id: uuid.UUID) -> str:
        return f"{self._key_prefix}:{token_id}"

    async def publish(self, token_id: uuid.UUID, token: str, ttl_seconds: int) -> None:
        """写入令牌本体（TTL 与 Capability 过期对齐，FR-33）。

        Raises:
            DependencyUnavailable: Redis 不可达（交接失败不得静默）。
        """
        try:
            await self._client.set(self._key(token_id), token, ex=ttl_seconds)
        except RedisError as exc:
            raise DependencyUnavailable(
                "Capability 交接通道不可用（拒绝裸奔执行，FR-34）"
            ) from exc

    async def fetch(self, token_id: uuid.UUID) -> str | None:
        """原子取出令牌本体（GETDEL；second fetch 恒 None）。

        Raises:
            DependencyUnavailable: Redis 不可达。
        """
        try:
            return cast("str | None", await self._client.getdel(self._key(token_id)))
        except RedisError as exc:
            raise DependencyUnavailable("Capability 交接通道不可用（FR-34）") from exc


class InMemoryCapabilityHandoff:
    """内存交接通道（测试 / 无 Redis 的本地开发；单次读语义与 Redis 一致）。"""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def publish(self, token_id: uuid.UUID, token: str, ttl_seconds: int) -> None:
        self._store[str(token_id)] = token

    async def fetch(self, token_id: uuid.UUID) -> str | None:
        return self._store.pop(str(token_id), None)
