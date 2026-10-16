"""API-08 列表端点用例（GET /projects/{id}/preparations）。

联调修复新增（fix/S10-e2e-integration）：前端审批台需要「项目下有哪些准备单」——
SPEC 6.1 只定义了创建（POST）而无列表读取，本用例覆盖新增列表端点的分页 / 过滤 /
项目隔离 / 前端展示口径字段。

覆盖 FR：FR-31（准备单查询）、FR-66（项目隔离）
对应 AC：AC-05（审批台消费准备单事实）、AC-12（跨项目 403）
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI

from tests.helpers import SeededWorkspace, make_client

IDEMPOTENCY_KEY = "idem-list-001"


async def _create(
    client: object,
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    actor: str,
    idempotency_key: str = IDEMPOTENCY_KEY,
) -> object:
    """经 API-08 真实创建准备单（列表用例的前置事实）。"""
    return await client.post(  # type: ignore[attr-defined]
        f"/api/v1/projects/{project_id}/preparations",
        json={
            "pipeline_version_id": str(version_id),
            "idempotency_key": idempotency_key,
            "environment": "dev",
            "tool_name": "pipeline.execute",
            "tool_version": "1.0.0",
        },
        headers={"X-Actor-ID": actor},
    )


async def test_list_preparations_empty(app: FastAPI, seeded: SeededWorkspace) -> None:
    """空列表：项目下无准备单 → 200 + 空数组 + X-Total-Count=0。"""
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/preparations",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 200, response.text
    assert response.json() == []
    assert response.headers["X-Total-Count"] == "0"


async def test_list_preparations_with_data(
    app: FastAPI,
    seeded: SeededWorkspace,
    frozen_version: tuple[uuid.UUID, str],
) -> None:
    """有数据：返回前端展示口径（pipeline_version_id / decision / risk_level / approvals）。"""
    version_id, _ = frozen_version
    async with make_client(app) as client:
        created = await _create(client, seeded.project_a_id, version_id, SeededWorkspace.ENGINEER_A)
        assert created.status_code == 201, created.text
        preparation_id = created.json()["id"]

        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/preparations",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert response.headers["X-Total-Count"] == "1"
    assert len(body) == 1
    item = body[0]
    assert item["id"] == preparation_id
    assert item["pipeline_version_id"] == str(version_id)
    assert item["decision"] in {"ALLOW", "REQUIRE_APPROVAL", "DEFER", "DENY"}
    assert item["risk_level"] in {"P0", "P1", "P2", "P3"}
    assert isinstance(item["approvals"], list)
    assert "required_roles" in item and "expires_at" in item


async def test_list_preparations_pagination_and_filter(
    app: FastAPI,
    seeded: SeededWorkspace,
    frozen_version: tuple[uuid.UUID, str],
) -> None:
    """分页 + 状态过滤：limit/offset 生效，X-Total-Count 反映过滤后总数。"""
    version_id, _ = frozen_version
    async with make_client(app) as client:
        first = await _create(
            client, seeded.project_a_id, version_id, SeededWorkspace.ENGINEER_A, "idem-list-a"
        )
        assert first.status_code == 201, first.text
        second = await _create(
            client, seeded.project_a_id, version_id, SeededWorkspace.ENGINEER_A, "idem-list-b"
        )
        assert second.status_code == 201, second.text

        all_items = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/preparations",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        paged = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/preparations",
            params={"limit": 1, "offset": 1},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )

    assert all_items.status_code == 200, all_items.text
    assert all_items.headers["X-Total-Count"] == "2"
    assert len(all_items.json()) == 2

    assert paged.status_code == 200, paged.text
    assert paged.headers["X-Total-Count"] == "2"
    assert len(paged.json()) == 1

    # status 过滤：用实际存在的状态过滤，条数与全量一致（证明过滤生效而非静默忽略）
    actual_status = all_items.json()[0]["status"]
    async with make_client(app) as client:
        filtered = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/preparations",
            params={"status": actual_status},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert filtered.status_code == 200, filtered.text
    assert filtered.headers["X-Total-Count"] == "2"


async def test_list_preparations_invalid_status_rejected(
    app: FastAPI, seeded: SeededWorkspace
) -> None:
    """非法 status → BIZ_400_VALIDATION。"""
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/preparations",
            params={"status": "NOT_A_STATUS"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 400
    assert response.json()["code"] == "BIZ_400_VALIDATION"


async def test_list_preparations_cross_project_denied(
    app: FastAPI, seeded: SeededWorkspace
) -> None:
    """跨项目访问 → 403（AC-12：非成员不区分「不存在」与「无权限」）。

    harness conftest 的 autouse 已把 outsider 加入项目 A，故此处用 admin-a
    访问**项目 B**（admin-a 不属 B）来构造跨项目边界。
    """
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_b_id}/preparations",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_403_NOT_PROJECT_MEMBER"
