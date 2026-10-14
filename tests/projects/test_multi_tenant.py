"""多租户隔离测试（T-04.4 / S2 提示词 10）。

覆盖：非成员访问项目资源 403、跨项目写 403、项目不存在不泄露（AC-12）。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI

from tests.helpers import SeededWorkspace, make_client


async def test_multi_tenant_403(app: FastAPI, seeded: SeededWorkspace) -> None:
    """非成员（outsider）访问项目 A 资源 → 403 AUTH_403_NOT_PROJECT_MEMBER。"""
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/members",
            headers={"X-Actor-ID": SeededWorkspace.OUTSIDER},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_403_NOT_PROJECT_MEMBER"


async def test_multi_tenant_cross_project_deny(app: FastAPI, seeded: SeededWorkspace) -> None:
    """跨项目写：A 项目成员操作 B 项目 → 403 AUTH_403_NOT_PROJECT_MEMBER（AC-12）。"""
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_b_id}/members",
            json={"subject": "intruder", "role": "DATA_ENGINEER"},
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_403_NOT_PROJECT_MEMBER"


async def test_multi_tenant_nonexistent_project_not_leaked(
    app: FastAPI, seeded: SeededWorkspace
) -> None:
    """项目不存在与无权限统一 403，不泄露项目是否存在（S2 提示词 9.4）。"""
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{uuid.uuid4()}/members",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_403_NOT_PROJECT_MEMBER"
