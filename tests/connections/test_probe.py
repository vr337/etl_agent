"""连通性测试（T-05.6/7 / API-03，S3 提示词 10）。

覆盖：PostgreSQL 实探成功 → READY；探针失败 → FAILED 且错误摘要脱敏；
暂不支持连接器 → 422 明确错误（非静默失败）。

背景任务在 FastAPI 依赖 ExitStack 内同步执行（httpx ASGITransport 下同样如此），
因此受理返回后状态已回写，可直接断言。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI

from etl_agent.connections.probe import run_probe
from tests.helpers import SeededWorkspace, dev_pg_credentials, make_client, make_settings

# 醒目且不可能出现在驱动报文里的「错误密码」，用于验证脱敏
WRONG_PASSWORD = "wrong-pw-zzz-9876"


async def test_probe_postgres_success(
    app: FastAPI, db_session: object, seeded_connection: tuple[object, object]
) -> None:
    """后台探针通过 → status=READY（FR-05，真实开发库）。"""
    connection, settings = seeded_connection
    outcome = await run_probe(
        db_session,  # type: ignore[arg-type]
        connection.project_id,  # type: ignore[attr-defined]
        connection.id,  # type: ignore[attr-defined]
        settings,  # type: ignore[arg-type]
    )
    assert outcome.status == "READY"
    assert outcome.error_summary is None


async def test_probe_failure_masked(
    app: FastAPI, db_session: object, seeded: SeededWorkspace
) -> None:
    """探针失败 → FAILED，错误摘要**不含密码**（AC-02 / S3 提示词 9.5）。"""
    from etl_agent.connections.register import register_connection
    from etl_agent.connections.schemas import ConnectionRegisterRequest

    settings = make_settings()
    pg = dev_pg_credentials()
    payload = ConnectionRegisterRequest(
        name=f"pg-bad-{uuid.uuid4()}",
        connector_type="POSTGRESQL",
        host=str(pg["host"]),
        port=int(pg["port"]),  # type: ignore[arg-type]
        database_name=str(pg["database_name"]),
        username=str(pg["username"]),
        password=WRONG_PASSWORD,
    )
    connection = await register_connection(
        db_session,  # type: ignore[arg-type]
        project_id=seeded.project_a_id,
        payload=payload,
        settings=settings,
    )
    outcome = await run_probe(
        db_session,  # type: ignore[arg-type]
        seeded.project_a_id,
        connection.id,
        settings,
    )
    assert outcome.status == "FAILED"
    assert outcome.error_summary is not None
    assert WRONG_PASSWORD not in outcome.error_summary


async def test_probe_unsupported_connector(
    app: FastAPI, db_session: object, seeded: SeededWorkspace
) -> None:
    """Oracle 等暂不支持类型 → 422 BIZ_422_CONNECTOR_UNSUPPORTED（明确错误，非静默失败）。"""
    from etl_agent.connections.register import register_connection
    from etl_agent.connections.schemas import ConnectionRegisterRequest

    settings = make_settings()
    payload = ConnectionRegisterRequest(
        name=f"oracle-{uuid.uuid4()}",
        connector_type="ORACLE",
        host="oracle-host",
        port=1521,
        database_name="ORCL",
        username="system",
        password="irrelevant-but-valid-length",
    )
    connection = await register_connection(
        db_session,  # type: ignore[arg-type]
        project_id=seeded.project_a_id,
        payload=payload,
        settings=settings,
    )
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/connections/{connection.id}/test",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 422
    assert response.json()["code"] == "BIZ_422_CONNECTOR_UNSUPPORTED"
    assert "not yet supported" in response.json()["message"]


async def test_probe_endpoint_ready(
    app: FastAPI, seeded: SeededWorkspace, seeded_connection: tuple[object, object]
) -> None:
    """API-03 端到端：受理返回 TESTING，后台回写 READY。"""
    connection, _ = seeded_connection
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/connections/{connection.id}/test",  # type: ignore[attr-defined]
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "TESTING"
        detail = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/connections/{connection.id}",  # type: ignore[attr-defined]
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert detail.status_code == 200
    assert detail.json()["status"] == "READY"
