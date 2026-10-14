"""trusted-gateway 模式测试（T-03.3 + T-03.5 / S2 提示词 10）。

覆盖：合法签名 200、篡改载荷 401、过期时间戳 401、缺 Header 401（AC-11）。
"""
from __future__ import annotations

import time
import uuid

from fastapi import FastAPI

from etl_agent.auth import trusted_gateway
from tests.helpers import TEST_GATEWAY_SECRET, SeededWorkspace, make_client

MEMBER_LIST_TARGET_TEMPLATE = "/api/v1/projects/{project_id}/members"


def _gateway_headers(
    *,
    actor: str,
    target: str,
    secret: str = TEST_GATEWAY_SECRET,
    method: str = "GET",
    timestamp: int | None = None,
    nonce: str | None = None,
    sign_target: str | None = None,
) -> dict[str, str]:
    """构造网关签名头；sign_target 可与实际 target 不同以制造「篡改载荷」场景。"""
    ts = str(timestamp if timestamp is not None else int(time.time()))
    n = nonce or uuid.uuid4().hex
    payload = trusted_gateway.build_canonical_payload(
        actor, ts, n, method, sign_target if sign_target is not None else target
    )
    return {
        trusted_gateway.HEADER_ACTOR: actor,
        trusted_gateway.HEADER_TIMESTAMP: ts,
        trusted_gateway.HEADER_NONCE: n,
        trusted_gateway.HEADER_SIGNATURE: trusted_gateway.compute_signature(secret, payload),
    }


async def test_trusted_gateway_valid(gateway_app: FastAPI, seeded: SeededWorkspace) -> None:
    """合法签名 → 200。"""
    target = MEMBER_LIST_TARGET_TEMPLATE.format(project_id=seeded.project_a_id)
    async with make_client(gateway_app) as client:
        response = await client.get(
            target, headers=_gateway_headers(actor=SeededWorkspace.ADMIN_A, target=target)
        )
    assert response.status_code == 200
    subjects = {member["subject"] for member in response.json()}
    assert SeededWorkspace.ADMIN_A in subjects


async def test_trusted_gateway_bad_signature(gateway_app: FastAPI, seeded: SeededWorkspace) -> None:
    """篡改载荷（签名按另一路径计算）→ 401 AUTH_401_BAD_SIGNATURE。"""
    target = MEMBER_LIST_TARGET_TEMPLATE.format(project_id=seeded.project_a_id)
    async with make_client(gateway_app) as client:
        response = await client.get(
            target,
            headers=_gateway_headers(
                actor=SeededWorkspace.ADMIN_A,
                target=target,
                sign_target="/api/v1/projects/other/members",
            ),
        )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_401_BAD_SIGNATURE"


async def test_trusted_gateway_expired_timestamp(gateway_app: FastAPI, seeded: SeededWorkspace) -> None:
    """时间戳偏移 > 60s → 401 AUTH_401_TIMESTAMP_SKEW。"""
    target = MEMBER_LIST_TARGET_TEMPLATE.format(project_id=seeded.project_a_id)
    async with make_client(gateway_app) as client:
        response = await client.get(
            target,
            headers=_gateway_headers(
                actor=SeededWorkspace.ADMIN_A,
                target=target,
                timestamp=int(time.time()) - 120,
            ),
        )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_401_TIMESTAMP_SKEW"


async def test_trusted_gateway_missing_header(gateway_app: FastAPI, seeded: SeededWorkspace) -> None:
    """缺任一签名 Header → 401 AUTH_401_BAD_SIGNATURE。"""
    target = MEMBER_LIST_TARGET_TEMPLATE.format(project_id=seeded.project_a_id)
    headers = _gateway_headers(actor=SeededWorkspace.ADMIN_A, target=target)
    del headers[trusted_gateway.HEADER_NONCE]
    async with make_client(gateway_app) as client:
        response = await client.get(target, headers=headers)
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_401_BAD_SIGNATURE"
