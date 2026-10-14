"""Harness 职责授予测试（T-04.3 / S2 提示词 10）。

覆盖：granted_by 记录正确、ADMIN 不隐式升级（FR-03）。
"""
from __future__ import annotations

from fastapi import FastAPI

from tests.helpers import SeededWorkspace, make_client


async def test_role_grant_granted_by(app: FastAPI, seeded: SeededWorkspace) -> None:
    """授予 SECURITY_APPROVER：granted_by = 当前 ADMIN 的 user_id（FR-03 留痕）。"""
    project_id = seeded.project_a_id
    async with make_client(app) as client:
        granted = await client.post(
            f"/api/v1/projects/{project_id}/role-grants",
            json={"subject": SeededWorkspace.ENGINEER_A, "role": "SECURITY_APPROVER"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        assert granted.status_code == 201
        body = granted.json()
        assert body["granted_by"] == str(seeded.admin_a_user_id)
        assert body["role"] == "SECURITY_APPROVER"

        listed = await client.get(
            f"/api/v1/projects/{project_id}/role-grants",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        assert any(g["role"] == "SECURITY_APPROVER" for g in listed.json())


async def test_role_grant_admin_not_implicit(app: FastAPI, seeded: SeededWorkspace) -> None:
    """ADMIN 自身无 SECURITY_APPROVER 授予——职责只能显式授予（FR-03 / PRD 2.3）。

    S2 无安全审批端点，require_harness_role 的拒绝行为在 tests/auth/test_actor_resolution.py
    以依赖直调方式验证；此处验证 ADMIN 授予他人后自身 harness_roles 仍为空。
    """
    project_id = seeded.project_a_id
    async with make_client(app) as client:
        headers = {"X-Actor-ID": SeededWorkspace.ADMIN_A}
        await client.post(
            f"/api/v1/projects/{project_id}/role-grants",
            json={"subject": SeededWorkspace.ENGINEER_A, "role": "SECURITY_APPROVER"},
            headers=headers,
        )
        # ADMIN 可以读取职责清单，但这不意味着自身获得该职责
        listed = await client.get(
            f"/api/v1/projects/{project_id}/role-grants", headers=headers
        )
        grantees = {g["subject"] for g in listed.json()}
        assert SeededWorkspace.ENGINEER_A in grantees
        assert SeededWorkspace.ADMIN_A not in grantees


async def test_role_grant_invalid_role(app: FastAPI, seeded: SeededWorkspace) -> None:
    """非法 Harness 职责 → 400 BIZ_400_VALIDATION（5 类取值约束）。"""
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/role-grants",
            json={"subject": SeededWorkspace.OUTSIDER, "role": "REVIEWER"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 400
    assert response.json()["code"] == "BIZ_400_VALIDATION"


async def test_role_grant_revoke(app: FastAPI, seeded: SeededWorkspace) -> None:
    """撤销职责授予 → 204，列表不再包含（FR-03）。"""
    project_id = seeded.project_a_id
    async with make_client(app) as client:
        headers = {"X-Actor-ID": SeededWorkspace.ADMIN_A}
        granted = await client.post(
            f"/api/v1/projects/{project_id}/role-grants",
            json={"subject": SeededWorkspace.OUTSIDER, "role": "AUDITOR"},
            headers=headers,
        )
        grant_id = granted.json()["id"]
        revoked = await client.delete(
            f"/api/v1/projects/{project_id}/role-grants/{grant_id}", headers=headers
        )
        assert revoked.status_code == 204
        listed = await client.get(
            f"/api/v1/projects/{project_id}/role-grants", headers=headers
        )
        assert all(g["id"] != grant_id for g in listed.json())
