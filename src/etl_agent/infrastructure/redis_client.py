"""M-A 认证鉴权 — Redis 客户端（Nonce 一次性消费的存储依赖）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-A / 第 8 章「安全 · 认证」行
覆盖 FR：FR-65（Nonce 16–128 位安全字符 + Redis SET NX 跨实例一次性，存储不可用失败关闭）
对应 AC：AC-11（Redis 不可用 → 503 失败关闭）
"""
from __future__ import annotations

from redis.asyncio import Redis
from redis.exceptions import RedisError

from etl_agent.config import settings


def create_redis_client(url: str | None = None) -> Redis:
    """创建异步 Redis 客户端（连接池懒建立，首个命令时才握手）。

    Args:
        url: 连接串；缺省用 settings.resolved_redis_url（infra/.env 连接件）。

    Returns:
        redis.asyncio.Redis：decode_responses=True（Nonce 值为 ASCII 计数，无需字节处理）。
    """
    # from_url 的返回类型在 redis 6.4 内联类型中为 Any，经显式注解收敛
    client: Redis = Redis.from_url(url or settings.resolved_redis_url, decode_responses=True)
    return client


async def ping(client: Redis) -> bool:
    """存活探测：任何 RedisError 均视为不可用（不向外抛，由调用方决定失败关闭行为）。"""
    try:
        await client.ping()
    except RedisError:
        return False
    return True


async def close_redis_client(client: Redis) -> None:
    """优雅关闭连接池（应用关闭事件调用；RedisError 不阻断关闭流程）。"""
    try:
        await client.aclose()
    except RedisError:
        pass
