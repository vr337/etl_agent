"""`/health` 用例（T-20.5 / FR-59 / FR-38）。

覆盖：存活 + 脱敏配置状态 + Worker 健康摘要、**不主动读凭据**、
库不可用时仍回存活（探针语义）。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import WorkerHeartbeat
from etl_agent.observability.health import (
    health_payload,
    secret_provider_summary,
    worker_summary,
)
from tests.helpers import make_client, make_settings

SECRET_VALUE = "super-secret-fernet-key-value"


def test_secret_provider_summary_is_redacted() -> None:
    """配置状态只回「是否已配置」与 provider 名，绝不回密钥材料（FR-59）。"""
    summary = secret_provider_summary(make_settings(fernet_key=SECRET_VALUE))
    assert summary == {"provider": "fernet-db", "configured": True, "key_version": 1}
    assert SECRET_VALUE not in str(summary)


def test_secret_provider_summary_reports_missing() -> None:
    """未配置时如实回 False（不猜测、不给默认值）。"""
    summary = secret_provider_summary(make_settings(fernet_key=""))
    assert summary["configured"] is False


def test_secret_provider_summary_vault_mode() -> None:
    """vault-kv-v2 模式看 VAULT_ADDR 是否配置（不连 Vault、不读凭据）。"""
    summary = secret_provider_summary(
        make_settings(secret_provider="vault-kv-v2", vault_addr="http://vault:8200")
    )
    assert summary == {"provider": "vault-kv-v2", "configured": True, "key_version": None}


async def test_health_payload_without_workers(db_session: AsyncSession) -> None:
    """Worker 摘要的契约：活跃数不超过总数、阈值为配置值、无行时无最近心跳时间。

    不硬断言绝对计数：开发库可能残留真实 Worker 的心跳行（容器化验收会写库），
    硬断言会让探针用例依赖外部状态。
    """
    payload = await health_payload(db_session, make_settings())
    workers = payload["workers"]
    assert payload["status"] == "ok"
    assert workers["active"] <= workers["total"]
    assert workers["inactive_after_seconds"] == 35
    assert (workers["latest_heartbeat_at"] is None) == (workers["total"] == 0)
    assert payload["observability"]["otel_enabled"] is False


async def test_health_payload_counts_fresh_heartbeat(db_session: AsyncSession) -> None:
    """新心跳计入活跃、过期心跳不计入（阈值与失活判定一致，FR-38）。"""
    now = datetime.now(UTC)
    settings = make_settings(worker_inactive_seconds=35)
    baseline = await worker_summary(db_session, settings, now=now)

    db_session.add_all(
        [
            WorkerHeartbeat(
                worker_id=f"w-fresh-{uuid.uuid4().hex[:8]}",
                last_heartbeat_at=now,
                capabilities={"engine": "seatunnel"},
            ),
            WorkerHeartbeat(
                worker_id=f"w-stale-{uuid.uuid4().hex[:8]}",
                last_heartbeat_at=now - timedelta(minutes=10),
                capabilities={"engine": "seatunnel"},
            ),
        ]
    )
    await db_session.flush()

    summary = await worker_summary(db_session, settings, now=now)
    assert summary["total"] == baseline["total"] + 2
    assert summary["active"] == baseline["active"] + 1


async def test_health_payload_skips_workers_when_no_session() -> None:
    """无会话时跳过 Worker 摘要（探针在库不可用时仍可回存活）。"""
    payload = await health_payload(None, make_settings())
    assert payload["status"] == "ok"
    assert "workers" not in payload


async def test_health_endpoint_returns_ok(
    app: FastAPI, db_session: AsyncSession
) -> None:
    """HTTP `/health` 返回存活 + 脱敏配置状态 + Worker 摘要。"""
    async with make_client(app) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "workers" in body
    assert body["secret_provider"]["provider"] == "fernet-db"


async def test_health_does_not_read_credentials(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-59 红线：`/health` 不实例化 SecretProvider、不调 `read()`。

    做法：让 `create_provider` 一旦被调用就抛错——探针若读凭据，本用例必然失败。
    """
    from etl_agent.secrets import factory

    def _explode(*args: object, **kwargs: object) -> object:
        raise AssertionError("/health 不得读取凭据（FR-59）")

    monkeypatch.setattr(factory, "create_provider", _explode)
    async with make_client(app) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
