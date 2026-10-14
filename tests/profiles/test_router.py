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
