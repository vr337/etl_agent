"""连接登记测试（T-05.5 / API-02，S3 提示词 10）。

覆盖：合法登记 201 且响应零明文、同名 409、非法连接器 400、跨项目 403。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI

from tests.helpers import SeededWorkspace, dev_pg_credentials, make_client


def _pg_payload(name: str, password: str) -> dict[str, object]:
    """指向开发库 PostgreSQL 的登记请求体（凭据来自 infra/.env）。"""
    pg = dev_pg_credentials()
    return {
        "name": name,
        "connector_type": "POSTGRESQL",
        "host": pg["host"],
        "port": pg["port"],
        "database_name": pg["database_name"],
        "username": pg["username"],
        "password": password,
    }


async def test_register_valid(app: FastAPI, seeded: SeededWorkspace) -> None:
    """合法登记 → 201；响应含 secret_ref，且不含 password（AC-02）。"""
    password = str(dev_pg_credentials()["password"])
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/connections",
            json=_pg_payload(f"pg-{uuid.uuid4()}", password),
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["secret_ref"]
    assert body["status"] == "UNTESTED"
    assert "password" not in body
    assert password not in response.text


async def test_register_duplicate_name(app: FastAPI, seeded: SeededWorkspace) -> None:
    """同名连接 → 409 BIZ_409_CONNECTION_NAME_DUPLICATE（FR-04）。"""
    name = f"dup-{uuid.uuid4()}"
    async with make_client(app) as client:
        headers = {"X-Actor-ID": SeededWorkspace.ADMIN_A}
        first = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/connections",
            json=_pg_payload(name, "irrelevant-but-valid-length"),
            headers=headers,
        )
        second = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/connections",
            json=_pg_payload(name, "irrelevant-but-valid-length"),
            headers=headers,
        )
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["code"] == "BIZ_409_CONNECTION_NAME_DUPLICATE"


async def test_register_invalid_connector_type(app: FastAPI, seeded: SeededWorkspace) -> None:
    """非法连接器类型 → 400 BIZ_400_VALIDATION。"""
    payload = _pg_payload(f"bad-{uuid.uuid4()}", "irrelevant-but-valid-length")
    payload["connector_type"] = "MONGODB"
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/connections",
            json=payload,
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 400
    assert response.json()["code"] == "BIZ_400_VALIDATION"


async def test_register_unknown_project(app: FastAPI, seeded: SeededWorkspace) -> None:
    """跨项目登记（非成员）→ 403 AUTH_403_NOT_PROJECT_MEMBER（AC-12）。"""
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_b_id}/connections",
            json=_pg_payload(f"cross-{uuid.uuid4()}", "irrelevant-but-valid-length"),
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_403_NOT_PROJECT_MEMBER"
