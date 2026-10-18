"""连接路由集成测试（T-05.8；列表 / 详情 / 权限，S3 提示词 10）。

覆盖：列表与详情（username 展示、password 不返回）、非成员 403、非 ADMIN 写 403。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI

from tests.helpers import SeededWorkspace, dev_pg_credentials, make_client


async def test_router_list_and_detail(
    app: FastAPI, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """列表 + 详情：含 username（定位信息），绝不含 password（AC-02 / S3 提示词 9.13）。"""
    connection, _ = seeded_connection
    pg = dev_pg_credentials()
    async with make_client(app) as client:
        headers = {"X-Actor-ID": SeededWorkspace.ADMIN_A}
        listed = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/connections", headers=headers
        )
        assert listed.status_code == 200
        assert any(c["id"] == str(connection.id) for c in listed.json())  # type: ignore[attr-defined]

        detail = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/connections/{connection.id}",  # type: ignore[attr-defined]
            headers=headers,
        )
    assert detail.status_code == 200
    body = detail.json()
    assert body["username"] == pg["username"]
    assert body["secret_ref"] == str(connection.secret_ref)  # type: ignore[attr-defined]
    assert "password" not in body
    assert str(pg["password"]) not in detail.text


async def test_router_non_member_403(
    app: FastAPI, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """非成员查看连接列表 → 403（AC-12）。"""
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/connections",
            headers={"X-Actor-ID": SeededWorkspace.OUTSIDER},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_403_NOT_PROJECT_MEMBER"


async def test_router_non_admin_write_403(
    app: FastAPI, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """非 ADMIN 成员登记连接 → 403 AUTH_403_ROLE_REQUIRED（写操作需 ADMIN）。"""
    pg = dev_pg_credentials()
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/connections",
            json={
                "name": f"x-{uuid.uuid4()}",
                "connector_type": "POSTGRESQL",
                "host": pg["host"],
                "port": pg["port"],
                "database_name": pg["database_name"],
                "username": pg["username"],
                "password": pg["password"],
            },
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_403_ROLE_REQUIRED"


async def test_router_connection_not_found_404(
    app: FastAPI, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """不存在的连接 → 404 BIZ_404_CONNECTION_NOT_FOUND（跨项目视同不存在）。"""
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/connections/{uuid.uuid4()}",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 404
    assert response.json()["code"] == "BIZ_404_CONNECTION_NOT_FOUND"


async def test_list_objects_rejects_non_s3_connection(
    app: FastAPI, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """对象前缀列举仅支持 S3 连接；其它类型 → 422 明确错误（非静默空列表）。"""
    connection, _ = seeded_connection
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/connections/{connection.id}/objects",  # type: ignore[attr-defined]
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 422
    assert response.json()["code"] == "BIZ_422_CONNECTOR_UNSUPPORTED"
