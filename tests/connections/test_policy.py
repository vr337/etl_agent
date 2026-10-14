"""轮换策略边界测试（T-06.7 / FR-11，S3 提示词 10）。

覆盖：边界内合法（max_age 1–3650、0 < warning < max_age）、越界 → 400。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.connections.errors import InvalidRotationPolicy
from etl_agent.connections.policy import update_policy, validate_policy
from etl_agent.connections.schemas import ConnectionPolicyUpdateRequest
from tests.helpers import SeededWorkspace, make_client


def _payload(max_age: int, warning: int) -> ConnectionPolicyUpdateRequest:
    return ConnectionPolicyUpdateRequest(
        credential_max_age_days=max_age, credential_warning_days=warning
    )


def test_policy_boundaries() -> None:
    """边界值合法：warning=1/max_age=2、max_age=3650/warning=3649（FR-11）。"""
    validate_policy(2, 1)
    validate_policy(3650, 3649)


def test_policy_invalid() -> None:
    """越界 → InvalidRotationPolicy（400，FR-11）。"""
    with pytest.raises(InvalidRotationPolicy):
        validate_policy(0, 1)  # max_age < 1
    with pytest.raises(InvalidRotationPolicy):
        validate_policy(3651, 1)  # max_age > 3650
    with pytest.raises(InvalidRotationPolicy):
        validate_policy(90, 0)  # warning ≤ 0
    with pytest.raises(InvalidRotationPolicy):
        validate_policy(90, 90)  # warning == max_age
    with pytest.raises(InvalidRotationPolicy):
        validate_policy(90, 120)  # warning > max_age
    with pytest.raises(InvalidRotationPolicy):
        validate_policy(1, 1)  # warning 必须严格 < max_age


def test_policy_valid_extremes() -> None:
    """极端合法值：max_age=2/warning=1、max_age=3650/warning=3649。"""
    validate_policy(2, 1)
    validate_policy(3650, 3649)


async def test_policy_update_via_api(
    app: FastAPI, db_session: AsyncSession, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """PATCH 端点：合法策略 → 200 且落库（FR-11）。"""
    connection, _ = seeded_connection
    async with make_client(app) as client:
        response = await client.patch(
            f"/api/v1/projects/{seeded.project_a_id}/connections/{connection.id}",  # type: ignore[attr-defined]
            json={"credential_max_age_days": 30, "credential_warning_days": 7},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["credential_max_age_days"] == 30
    assert body["credential_warning_days"] == 7


async def test_policy_update_invalid_via_api(
    app: FastAPI, db_session: AsyncSession, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """PATCH 端点：warning ≥ max_age → 400 BIZ_400_VALIDATION（FR-11）。"""
    connection, _ = seeded_connection
    async with make_client(app) as client:
        response = await client.patch(
            f"/api/v1/projects/{seeded.project_a_id}/connections/{connection.id}",  # type: ignore[attr-defined]
            json={"credential_max_age_days": 30, "credential_warning_days": 30},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 400
    assert response.json()["code"] == "BIZ_400_VALIDATION"


async def test_policy_update_service_layer(
    db_session: AsyncSession, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """服务层直改：合法策略落库（FR-11）。"""
    connection, _ = seeded_connection
    updated = await update_policy(
        db_session,
        project_id=seeded.project_a_id,
        connection_id=connection.id,  # type: ignore[attr-defined]
        payload=_payload(180, 30),
    )
    assert updated.credential_max_age_days == 180
    assert updated.credential_warning_days == 30
