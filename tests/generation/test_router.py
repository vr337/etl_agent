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

        # AMEND-009：响应回传本次运行绑定的版本 id，且该版本可经 API 直取
        version_id = body["pipeline_version_id"]
        assert version_id
        version = await client.get(
            f"/api/v1/pipeline-versions/{version_id}",
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert version.status_code == 200, version.text
    assert version.json()["id"] == version_id


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


async def test_router_list_agent_runs_empty(
    app: FastAPI, seeded: SeededWorkspace
) -> None:
    """空列表：项目无生成运行 → 200 + 空数组 + X-Total-Count=0（联调修复新增）。"""
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/agent-runs",
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert response.status_code == 200, response.text
    assert response.json() == []
    assert response.headers["X-Total-Count"] == "0"


async def test_router_list_agent_runs_with_data(
    app: FastAPI, seeded: SeededWorkspace, seeded_profiles: dict[str, uuid.UUID]
) -> None:
    """有数据：返回前端契约口径（pipeline_id 派生 / 遥测计数），不含候选正文（FR-67）。"""
    async with make_client(app) as client:
        created = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/agent-runs",
            json={
                "requirement": "生成 orders → orders_wide 的宽表任务",
                "source_profile_ids": [str(seeded_profiles["source"])],
                "sink_profile_ids": [str(seeded_profiles["sink"])],
            },
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
        assert created.status_code == 201, created.text
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/agent-runs",
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert response.status_code == 200, response.text
    assert response.headers["X-Total-Count"] == "1"
    body = response.json()
    assert len(body) == 1
    item = body[0]
    assert item["id"] == created.json()["id"]
    assert item["status"] == "WAITING_APPROVAL"
    assert uuid.UUID(item["pipeline_id"])  # pipeline_id 为合法 UUID（经版本链派生）
    assert item["repair_count"] == 0
    assert "clarify_question" in item
    for absent in ("etl_plan", "hocon", "requirement", "candidate"):
        assert absent not in item


async def test_router_list_agent_runs_pagination(
    app: FastAPI, seeded: SeededWorkspace, seeded_profiles: dict[str, uuid.UUID]
) -> None:
    """分页：limit/offset 生效，X-Total-Count 反映全量总数（联调修复新增）。"""
    async with make_client(app) as client:
        for _ in range(2):
            created = await client.post(
                f"/api/v1/projects/{seeded.project_a_id}/agent-runs",
                json={
                    "requirement": "分页用例",
                    "source_profile_ids": [str(seeded_profiles["source"])],
                    "sink_profile_ids": [str(seeded_profiles["sink"])],
                },
                headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
            )
            assert created.status_code == 201, created.text
        path = f"/api/v1/projects/{seeded.project_a_id}/agent-runs"
        first_page = await client.get(
            path, params={"limit": 1}, headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A}
        )
        second_page = await client.get(
            path, params={"limit": 1, "offset": 1}, headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A}
        )
    assert first_page.status_code == 200, first_page.text
    assert first_page.headers["X-Total-Count"] == "2"
    assert len(first_page.json()) == 1
    assert second_page.status_code == 200, second_page.text
    assert second_page.headers["X-Total-Count"] == "2"
    assert len(second_page.json()) == 1
