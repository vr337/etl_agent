"""Nonce 一次性消费测试（T-03.4 / S2 提示词 10）。

覆盖：重放 401、Redis 不可用 503（失败关闭）、长度越界 401（AC-11）。

说明：test_nonce_redis_unavailable 以故障注入（客户端 set 抛 RedisError）确定性
复现失败关闭路径；真实「停容器」场景由 DoD 验收命令 7 单独验证。
"""
from __future__ import annotations

import time
import uuid

import pytest
from fastapi import FastAPI
from redis.exceptions import RedisError

from etl_agent.auth import trusted_gateway
from etl_agent.auth.errors import NonceStoreUnavailable
from etl_agent.auth.nonce_store import verify_and_consume_nonce
from tests.helpers import TEST_GATEWAY_SECRET, SeededWorkspace, make_client

MEMBER_LIST_TARGET_TEMPLATE = "/api/v1/projects/{project_id}/members"


def _gateway_headers(
    *,
    actor: str,
    target: str,
    nonce: str,
    secret: str = TEST_GATEWAY_SECRET,
    method: str = "GET",
    timestamp: int | None = None,
) -> dict[str, str]:
    """构造网关签名头（Nonce 由调用方指定，便于重放 / 越界场景）。"""
    ts = str(timestamp if timestamp is not None else int(time.time()))
    payload = trusted_gateway.build_canonical_payload(actor, ts, nonce, method, target)
    return {
        trusted_gateway.HEADER_ACTOR: actor,
        trusted_gateway.HEADER_TIMESTAMP: ts,
        trusted_gateway.HEADER_NONCE: nonce,
        trusted_gateway.HEADER_SIGNATURE: trusted_gateway.compute_signature(secret, payload),
    }


class _BrokenRedis:
    """故障注入：任何写操作都抛 RedisError（模拟 Redis 宕机）。"""

    async def set(self, *args: object, **kwargs: object) -> None:
        raise RedisError("connection refused")

    async def aclose(self) -> None:
        """关闭为空操作（fixture teardown 调用，故障对象无连接池可释放）。"""


async def test_nonce_replay(gateway_app: FastAPI, seeded: SeededWorkspace) -> None:
    """同一 Nonce 二次使用 → 首次 200，二次 401 AUTH_401_BAD_SIGNATURE（重放）。"""
    target = MEMBER_LIST_TARGET_TEMPLATE.format(project_id=seeded.project_a_id)
    nonce = uuid.uuid4().hex
    headers = _gateway_headers(actor=SeededWorkspace.ADMIN_A, target=target, nonce=nonce)
    async with make_client(gateway_app) as client:
        first = await client.get(target, headers=headers)
        second = await client.get(target, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 401
    assert second.json()["code"] == "AUTH_401_BAD_SIGNATURE"


async def test_nonce_redis_unavailable(gateway_app: FastAPI, seeded: SeededWorkspace) -> None:
    """Redis 不可用 → 503 AUTH_503_NONCE_STORE_UNAVAILABLE（失败关闭，不降级放行）。"""
    target = MEMBER_LIST_TARGET_TEMPLATE.format(project_id=seeded.project_a_id)
    gateway_app.state.redis = _BrokenRedis()
    async with make_client(gateway_app) as client:
        response = await client.get(
            target,
            headers=_gateway_headers(
                actor=SeededWorkspace.ADMIN_A, target=target, nonce=uuid.uuid4().hex
            ),
        )
    assert response.status_code == 503
    assert response.json()["code"] == "AUTH_503_NONCE_STORE_UNAVAILABLE"


async def test_nonce_length_invalid(gateway_app: FastAPI, seeded: SeededWorkspace) -> None:
    """Nonce 长度越界（<16）→ 401 AUTH_401_BAD_SIGNATURE。"""
    target = MEMBER_LIST_TARGET_TEMPLATE.format(project_id=seeded.project_a_id)
    async with make_client(gateway_app) as client:
        response = await client.get(
            target,
            headers=_gateway_headers(
                actor=SeededWorkspace.ADMIN_A, target=target, nonce="tooshort"
            ),
        )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_401_BAD_SIGNATURE"


async def test_nonce_store_unit_fail_closed() -> None:
    """存储层单测：SET NX 抛 RedisError → NonceStoreUnavailable（AC-11 失败关闭）。"""
    with pytest.raises(NonceStoreUnavailable):
        await verify_and_consume_nonce(uuid.uuid4().hex, _BrokenRedis())  # type: ignore[arg-type]
