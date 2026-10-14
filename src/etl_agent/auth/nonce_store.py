"""M-A 认证鉴权 — Nonce 一次性消费（Redis SET NX）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-A / 第 8 章「安全 · 认证」行
覆盖 FR：FR-65（Nonce 16–128 位安全字符 + Redis SET NX 跨实例一次性消费）
对应 AC：AC-11（Redis 不可用 → 503 失败关闭；已消费 Nonce → 401）
"""
from __future__ import annotations

from redis.asyncio import Redis
from redis.exceptions import RedisError

from etl_agent.auth.errors import BadSignature, NonceStoreUnavailable

# Nonce 键前缀：与 Celery Broker 键空间隔离，避免误碰
NONCE_KEY_PREFIX = "auth:nonce:"

# TTL 120s：时钟偏移容忍 60s 的两倍余量，保证重放窗口内 Nonce 必然已被消费或过期
NONCE_TTL_SECONDS = 120


def nonce_key(nonce: str) -> str:
    """Nonce 的 Redis 键（命名空间隔离）。"""
    return f"{NONCE_KEY_PREFIX}{nonce}"


async def verify_and_consume_nonce(
    nonce: str, client: Redis, ttl: int = NONCE_TTL_SECONDS
) -> None:
    """Nonce 一次性消费。

    SPEC 8「安全 · 认证」：Redis SET NX 语义，跨实例一次性；存储不可用时失败关闭。

    Args:
        nonce: 16–128 位安全字符（字符集校验在 trusted_gateway 侧先行完成）。
        client: 异步 Redis 客户端。
        ttl: 秒，默认 120（覆盖 ±60s 时钟偏移窗口的两倍）。

    Raises:
        BadSignature:      Nonce 已消费（重放攻击，并入 401 不泄露重放状态）。
        NonceStoreUnavailable: Redis 不可用（失败关闭返回 503，不降级放行）。
    """
    try:
        # SET NX：仅当键不存在时写入——多副本 API 共享同一 Redis 时等价于全局一次性
        stored = await client.set(nonce_key(nonce), "1", nx=True, ex=ttl)
    except RedisError as exc:
        # 失败关闭：Redis 不可用时拒绝请求而非放行，否则重放防护失效（AC-11）
        raise NonceStoreUnavailable() from exc
    if not stored:
        raise BadSignature("Nonce 已消费（重放）")
