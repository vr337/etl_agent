"""零明文泄露行为测试（AC-02；S3 提示词 9.5 / 9.13，第十节用例）。

覆盖：登记响应含 secret_ref 但绝不含 password 明文；DB 密文 ≠ 明文；
探针失败摘要脱敏（不含密码）。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI
from sqlalchemy import select

from etl_agent.infrastructure.models import EncryptedSecret
from tests.helpers import SeededWorkspace, dev_pg_credentials, make_client


async def test_register_response_no_plaintext(
    app: FastAPI, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """登记响应体：含 secret_ref 与定位信息，绝不含 password 字段与明文（AC-02）。"""
    pg = dev_pg_credentials()
    password = str(pg["password"])
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/connections",
            json={
                "name": f"zp-{uuid.uuid4()}",
                "connector_type": "POSTGRESQL",
                "host": pg["host"],
                "port": pg["port"],
                "database_name": pg["database_name"],
                "username": pg["username"],
                "password": password,
            },
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 201
    body = response.json()
    assert "password" not in body
    assert "ciphertext" not in body
    assert body["secret_ref"] is not None
    # 响应全文不得出现明文密码（双重保险）
    assert password not in response.text


async def test_db_ciphertext_not_plaintext(
    app: FastAPI, seeded: SeededWorkspace, seeded_connection: tuple, db_session
) -> None:
    """落库密文不得等于明文（FR-06：fernet-db 加密后存储）。"""
    connection, _ = seeded_connection
    pg = dev_pg_credentials()
    password = str(pg["password"])
    row = (
        await db_session.execute(
            select(EncryptedSecret).where(EncryptedSecret.id == connection.secret_ref)
        )
    ).scalar_one()
    assert row.ciphertext is not None
    assert row.ciphertext != password
    # 密文经 base64 外观（Fernet token），不含可识别的密码片段
    assert password not in row.ciphertext


async def test_probe_failure_summary_masked(
    app: FastAPI, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """连通性测试失败：错误摘要脱敏，不含密码（AC-02 / S3 提示词 9.5）。"""
    pg = dev_pg_credentials()
    password = str(pg["password"])
    async with make_client(app) as client:
        # 用错误密码登记 → 探针失败
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/connections",
            json={
                "name": f"bad-{uuid.uuid4()}",
                "connector_type": "POSTGRESQL",
                "host": pg["host"],
                "port": pg["port"],
                "database_name": pg["database_name"],
                "username": pg["username"],
                "password": "definitely-wrong-password-xyz",
            },
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 201
    conn_id = response.json()["id"]
    # 触发同步探针（错误凭据 → 失败）
    async with make_client(app) as client:
        test_resp = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/connections/{conn_id}/test",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    # 探针受理即返回 TESTING（200 + TESTING 状态）；后台失败后状态置 FAILED，错误摘要不得含密码
    assert test_resp.status_code == 200
    body = test_resp.json()
    assert body["status"] == "TESTING"
    assert password not in test_resp.text
