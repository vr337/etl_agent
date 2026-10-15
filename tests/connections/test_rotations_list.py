"""凭据轮换列表端点用例（联调修复新增，fix/S10-e2e-integration）。

覆盖：GET /projects/{id}/connections/{cid}/rotations 的空列表 / 有数据 /
分页 / 跨项目 404；列表只含状态与引用，绝不含凭据（AC-02）。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.secrets import rotation as rotation_service
from tests.helpers import SeededWorkspace, dev_pg_credentials, make_client, make_settings


async def _create_rotation(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    connection_id: uuid.UUID,
    reason: str,
):
    """经服务层创建一轮轮换（VALIDATING；新凭据即开发库真实凭据，只暂存不校验连通性）。"""
    pg = dev_pg_credentials()
    return await rotation_service.create_rotation(
        db_session,
        project_id=seeded.project_a_id,
        connection_id=connection_id,
        new_username=str(pg["username"]),
        new_password=str(pg["password"]),
        reason=reason,
        requested_by=seeded.admin_a_user_id,
        settings=make_settings(),
    )


async def test_list_rotations_empty(
    app: FastAPI, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """空列表：连接无轮换申请 → 200 + 空数组 + X-Total-Count=0。"""
    connection, _ = seeded_connection
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/connections/{connection.id}/rotations",  # type: ignore[attr-defined]
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 200, response.text
    assert response.json() == []
    assert response.headers["X-Total-Count"] == "0"


async def test_list_rotations_with_data(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
) -> None:
    """有数据：返回前端时间轴口径（staged_at 由 created_at 派生），绝不含凭据（AC-02）。"""
    connection, _ = seeded_connection
    rotation = await _create_rotation(db_session, seeded, connection.id, "scheduled rotation")  # type: ignore[attr-defined]
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/connections/{connection.id}/rotations",  # type: ignore[attr-defined]
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert response.headers["X-Total-Count"] == "1"
    assert len(body) == 1
    item = body[0]
    assert item["id"] == str(rotation.id)
    assert item["connection_id"] == str(connection.id)  # type: ignore[attr-defined]
    assert item["status"] == "VALIDATING"
    assert item["reason"] == "scheduled rotation"
    assert item["staged_at"] is not None
    # 后端无逐阶段时间列：未激活申请的 validated_at / activated_at 如实回 None
    assert item["validated_at"] is None
    assert item["activated_at"] is None
    # 零明文（AC-02）：列表不暴露 secret 引用与治理标识
    assert "new_secret_ref" not in item
    assert "requested_by" not in item
    assert "password" not in item


async def test_list_rotations_pagination(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
) -> None:
    """分页：limit/offset 生效，X-Total-Count 反映全量总数。"""
    connection, _ = seeded_connection
    for index in range(3):
        await _create_rotation(db_session, seeded, connection.id, f"rotation-{index}")  # type: ignore[attr-defined]
    path = f"/api/v1/projects/{seeded.project_a_id}/connections/{connection.id}/rotations"  # type: ignore[attr-defined]
    async with make_client(app) as client:
        first_page = await client.get(
            path, params={"limit": 2}, headers={"X-Actor-ID": SeededWorkspace.ADMIN_A}
        )
        second_page = await client.get(
            path, params={"limit": 2, "offset": 2}, headers={"X-Actor-ID": SeededWorkspace.ADMIN_A}
        )
    assert first_page.status_code == 200, first_page.text
    assert first_page.headers["X-Total-Count"] == "3"
    assert len(first_page.json()) == 2
    assert second_page.status_code == 200, second_page.text
    assert second_page.headers["X-Total-Count"] == "3"
    assert len(second_page.json()) == 1


async def test_list_rotations_cross_project_404(
    app: FastAPI, seeded: SeededWorkspace, seeded_connection_b: tuple
) -> None:
    """项目 A 成员访问项目 B 连接的轮换列表 → 404（跨项目视同不存在，AC-12）。"""
    connection_b, _ = seeded_connection_b
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/connections/{connection_b.id}/rotations",  # type: ignore[attr-defined]
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 404
    assert response.json()["code"] == "BIZ_404_CONNECTION_NOT_FOUND"
