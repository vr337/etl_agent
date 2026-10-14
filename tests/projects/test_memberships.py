"""成员管理测试（T-04.2 / S2 提示词 10）。

覆盖：增删成功、(project_id, user_id) UK 重复添加 409（FR-02）。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI

from tests.helpers import SeededWorkspace, make_client

NEW_MEMBER_SUBJECT = "new-engineer"


async def test_membership_add_remove(app: FastAPI, seeded: SeededWorkspace) -> None:
    """添加 → 列表可见 → 移除 → 列表不可见（FR-02）。"""
    project_id = seeded.project_a_id
    async with make_client(app) as client:
        admin_headers = {"X-Actor-ID": SeededWorkspace.ADMIN_A}

        added = await client.post(
            f"/api/v1/projects/{project_id}/members",
            json={"subject": NEW_MEMBER_SUBJECT, "role": "DATA_ENGINEER"},
            headers=admin_headers,
        )
        assert added.status_code == 201
        new_user_id = added.json()["user_id"]

        listed = await client.get(
            f"/api/v1/projects/{project_id}/members", headers=admin_headers
        )
        assert any(m["subject"] == NEW_MEMBER_SUBJECT for m in listed.json())

        removed = await client.delete(
            f"/api/v1/projects/{project_id}/members/{new_user_id}", headers=admin_headers
        )
        assert removed.status_code == 204

        listed_after = await client.get(
            f"/api/v1/projects/{project_id}/members", headers=admin_headers
        )
        assert not any(m["subject"] == NEW_MEMBER_SUBJECT for m in listed_after.json())


async def test_membership_duplicate(app: FastAPI, seeded: SeededWorkspace) -> None:
    """重复添加同一成员 → 409 BIZ_409_UNIQUE_CONSTRAINT（(project_id, user_id) UK）。"""
    project_id = seeded.project_a_id
    payload = {"subject": NEW_MEMBER_SUBJECT, "role": "DATA_ENGINEER"}
    async with make_client(app) as client:
        headers = {"X-Actor-ID": SeededWorkspace.ADMIN_A}
        first = await client.post(
            f"/api/v1/projects/{project_id}/members", json=payload, headers=headers
        )
        second = await client.post(
            f"/api/v1/projects/{project_id}/members", json=payload, headers=headers
        )
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["code"] == "BIZ_409_UNIQUE_CONSTRAINT"


async def test_membership_add_requires_admin(app: FastAPI, seeded: SeededWorkspace) -> None:
    """非 ADMIN 成员写操作 → 403 AUTH_403_ROLE_REQUIRED（S2 提示词 9.6）。"""
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/members",
            json={"subject": "another-engineer", "role": "DATA_ENGINEER"},
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_403_ROLE_REQUIRED"


async def test_membership_remove_self_forbidden(app: FastAPI, seeded: SeededWorkspace) -> None:
    """不能移除自己 → 409 BIZ_409_STATE_CONFLICT（S2 提示词 9.6）。"""
    async with make_client(app) as client:
        response = await client.delete(
            f"/api/v1/projects/{seeded.project_a_id}/members/{seeded.admin_a_user_id}",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 409
    assert response.json()["code"] == "BIZ_409_STATE_CONFLICT"


async def test_membership_invalid_role(app: FastAPI, seeded: SeededWorkspace) -> None:
    """非法成员角色 → 400 BIZ_400_VALIDATION（4 类取值约束）。"""
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/members",
            json={"subject": f"role-test-{uuid.uuid4()}", "role": "SUPERUSER"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 400
    assert response.json()["code"] == "BIZ_400_VALIDATION"
