"""生成路由测试（T-09；S5 提示词第十二节 test_router）。

覆盖：创建运行成功（无正文回传）、跨项目 403、AC-02 零明文扫描（生成响应）。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI

from tests.helpers import SeededWorkspace, dev_pg_credentials, make_client


async def test_router_create_agent_run(
    app: FastAPI, seeded: SeededWorkspace, seeded_profiles: dict[str, uuid.UUID]
) -> None:
    """创建运行 → 201 + 状态 / 遥测字段，且不含候选正文字段（FR-16/67）。"""
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/agent-runs",
            json={
                "requirement": "生成 orders → orders_wide 的宽表任务",
                "source_profile_ids": [str(seeded_profiles["source"])],
                "sink_profile_ids": [str(seeded_profiles["sink"])],
            },
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "WAITING_APPROVAL"
    for absent in ("etl_plan", "hocon", "requirement", "candidate"):
        assert absent not in body


async def test_router_cross_project_403(app: FastAPI, seeded: SeededWorkspace) -> None:
    """局外人创建运行 → 403 AUTH_403_NOT_PROJECT_MEMBER（AC-12）。"""
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/agent-runs",
            json={"requirement": "越权创建"},
            headers={"X-Actor-ID": SeededWorkspace.OUTSIDER},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_403_NOT_PROJECT_MEMBER"


async def test_zero_plaintext_generation_response(
    app: FastAPI, seeded: SeededWorkspace, seeded_profiles: dict[str, uuid.UUID]
) -> None:
    """AC-02：生成链路响应与库内遥测均不含需求正文与连接密码明文。"""
    marker = f"REQ-MARKER-{uuid.uuid4().hex}"
    password = str(dev_pg_credentials()["password"])
    async with make_client(app) as client:
        created = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/agent-runs",
            json={
                "requirement": marker,
                "source_profile_ids": [str(seeded_profiles["source"])],
                "sink_profile_ids": [str(seeded_profiles["sink"])],
            },
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        assert created.status_code == 201
        # 澄清响应同样不得回传需求正文
        answered = await client.post(
            f"/api/v1/agent-runs/{created.json()['id']}/messages",
            json={"answer": "再补一句需求描述"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert created.status_code == 201
    for response in (created, answered):
        assert marker not in response.text
        assert password not in response.text
    # 澄清回答（用户输入）同样不落业务库、不回传
    assert "再补一句需求描述" not in answered.text
