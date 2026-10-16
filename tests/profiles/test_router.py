"""Profile 路由集成测试（T-07.7；API-04，S4 提示词第十节）。

覆盖：探查成功（Schema + 脱敏样本 + SHA256 + 过期时间）、跨项目 403、
跨项目连接 404（不泄露存在性）、采样上限越界 422。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI

from etl_agent.profiles.masking import MASK_TOKEN
from tests.helpers import SAMPLE_EMAILS, SeededWorkspace, make_client


def _profile_payload(
    connection_id: uuid.UUID, table_name: str, **overrides: object
) -> dict[str, object]:
    payload: dict[str, object] = {
        "connection_id": str(connection_id),
        "endpoint_role": "SOURCE",
        "schema_name": "public",
        "table_name": table_name,
        "sample_limit": 10,
    }
    payload.update(overrides)
    return payload


async def test_router_create_profile(
    app: FastAPI,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
    profile_source_table: str,
) -> None:
    """探查成功：返回列统计 + 脱敏样本 + 分级报告摘要 + 过期时间（FR-12/14/15）。"""
    connection, _ = seeded_connection
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/profiles",
            json=_profile_payload(connection.id, profile_source_table),
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["schema_name"] == "public"
    assert body["table_name"] == profile_source_table
    assert body["endpoint_role"] == "SOURCE"
    assert body["expires_at"]
    assert len(body["classification_report_sha256"]) == 64
    assert body["approximate_row_count"] >= 0

    columns = {column["name"]: column for column in body["columns"]}
    assert columns["id"]["is_primary_key"] is True
    # 未声明分级 → UNKNOWN，并按保守等级参与策略（FR-15）
    assert columns["customer_email"]["classification"] == "UNKNOWN"
    assert columns["customer_email"]["effective_classification"] == "CONFIDENTIAL"
    # 脱敏：email 列整体替换为占位符
    assert body["masked_samples"]
    assert all(row["customer_email"] == MASK_TOKEN for row in body["masked_samples"])


async def test_router_cross_project_403(
    app: FastAPI, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """非项目成员调用探查端点 → 403 AUTH_403_NOT_PROJECT_MEMBER（AC-12）。"""
    connection, _ = seeded_connection
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/profiles",
            json=_profile_payload(connection.id, "projects"),
            headers={"X-Actor-ID": SeededWorkspace.OUTSIDER},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_403_NOT_PROJECT_MEMBER"


async def test_router_cross_project_connection_404(
    app: FastAPI, seeded: SeededWorkspace, seeded_connection_b: tuple
) -> None:
    """项目 A 成员引用项目 B 的连接 → 404（跨项目视同不存在，不泄露存在性）。"""
    connection_b, _ = seeded_connection_b
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/profiles",
            json=_profile_payload(connection_b.id, "projects"),
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 404
    assert response.json()["code"] == "BIZ_404_CONNECTION_NOT_FOUND"


async def test_router_list_profiles_empty(
    app: FastAPI, seeded: SeededWorkspace
) -> None:
    """空列表：项目无 Profile → 200 + 空数组 + X-Total-Count=0（联调修复新增）。"""
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/profiles",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 200, response.text
    assert response.json() == []
    assert response.headers["X-Total-Count"] == "0"


async def test_router_list_profiles_with_data(
    app: FastAPI,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
    profile_source_table: str,
) -> None:
    """有数据：返回前端展示口径（列数 / 主键 / 分级摘要 / 样本状态），不回列明细与样本。"""
    connection, _ = seeded_connection
    async with make_client(app) as client:
        created = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/profiles",
            json=_profile_payload(connection.id, profile_source_table),
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        assert created.status_code == 201, created.text
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/profiles",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 200, response.text
    assert response.headers["X-Total-Count"] == "1"
    body = response.json()
    assert len(body) == 1
    item = body[0]
    assert item["id"] == created.json()["id"]
    assert item["table_name"] == profile_source_table
    assert item["column_count"] == len(created.json()["columns"])
    assert item["primary_key"] == ["id"]
    assert len(item["report_sha256"]) == 64
    assert item["classifications"].get("CONFIDENTIAL", 0) >= 1
    assert item["sample_status"] == "AVAILABLE"
    # 列表保持轻量：不含列明细与脱敏样本（重载荷走单条探查入口）
    assert "columns" not in item
    assert "masked_samples" not in item


async def test_router_list_profiles_pagination(
    app: FastAPI,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
    profile_source_table: str,
) -> None:
    """分页：limit/offset 生效，X-Total-Count 反映全量总数（联调修复新增）。"""
    connection, _ = seeded_connection
    async with make_client(app) as client:
        created = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/profiles",
            json=_profile_payload(connection.id, profile_source_table),
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        assert created.status_code == 201, created.text
        path = f"/api/v1/projects/{seeded.project_a_id}/profiles"
        first_page = await client.get(
            path, params={"limit": 1}, headers={"X-Actor-ID": SeededWorkspace.ADMIN_A}
        )
        beyond = await client.get(
            path, params={"limit": 1, "offset": 1}, headers={"X-Actor-ID": SeededWorkspace.ADMIN_A}
        )
    assert first_page.status_code == 200, first_page.text
    assert first_page.headers["X-Total-Count"] == "1"
    assert len(first_page.json()) == 1
    assert beyond.status_code == 200, beyond.text
    assert beyond.headers["X-Total-Count"] == "1"
    assert beyond.json() == []


async def test_sample_limit_range(
    app: FastAPI,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
    profile_source_table: str,
) -> None:
    """采样上限越界 → 422 BIZ_422_SAMPLE_LIMIT_OUT_OF_RANGE（不做静默截断）。"""
    connection, _ = seeded_connection
    async with make_client(app) as client:
        for limit in (0, 101):
            response = await client.post(
                f"/api/v1/projects/{seeded.project_a_id}/profiles",
                json=_profile_payload(connection.id, profile_source_table, sample_limit=limit),
                headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
            )
            assert response.status_code == 422
            assert response.json()["code"] == "BIZ_422_SAMPLE_LIMIT_OUT_OF_RANGE"


async def test_router_invalid_endpoint_role(
    app: FastAPI,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
    profile_source_table: str,
) -> None:
    """端点角色非法 → 400 BIZ_400_VALIDATION（FR-12：仅 SOURCE/SINK）。"""
    connection, _ = seeded_connection
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/profiles",
            json=_profile_payload(connection.id, profile_source_table, endpoint_role="BOTH"),
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 400
    assert response.json()["code"] == "BIZ_400_VALIDATION"


async def test_router_source_table_not_found(
    app: FastAPI, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """源表不存在 → 422 BIZ_422_PROBE_FAILED（复用 AMEND-002，摘要脱敏）。"""
    connection, _ = seeded_connection
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/profiles",
            json=_profile_payload(connection.id, f"missing_{uuid.uuid4().hex[:8]}"),
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 422
    assert response.json()["code"] == "BIZ_422_PROBE_FAILED"


def test_sample_emails_are_plaintext_markers() -> None:
    """自检：样本邮箱必须是可识别的明文标记，否则零明文断言会形同虚设。"""
    assert SAMPLE_EMAILS
    assert all("@" in email for email in SAMPLE_EMAILS)
