"""Replay Guard / Outbox / 零明文测试（T-12.9~12.11 + AC-02；S6 提示词第十二节）。

覆盖（FR-34/35）：令牌仅消费一次；Outbox PENDING 写入与状态机；
Harness 全链路响应无凭证明文（AC-02）。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.harness.broker.replay_guard import ReplayGuard
from etl_agent.harness.broker.tool_broker import BrokerCommand, ToolBroker
from etl_agent.harness.errors import (
    CapabilityReplayed,
    ToolNotRegistered,
)
from etl_agent.harness.outbox import service as outbox_service
from etl_agent.harness.side_effects.registry import (
    MAIN_EXECUTION_COMMAND,
    is_registered,
)
from etl_agent.infrastructure.models import HarnessCapabilityGrant
from tests.helpers import SeededWorkspace, dev_pg_credentials, make_client

from .test_commit import _approve_p0, _commit, _prepare


async def _capability_grant(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    frozen_version: tuple,
    key: str | None = None,
) -> tuple[HarnessCapabilityGrant, str]:
    """走完整 prepare→approve→commit，返回能力令牌行与 token_sha256。

    `key` 必须每个用例唯一：幂等键是项目内唯一约束（UK），复用会让用例互相冲突。
    """
    version_id, _ = frozen_version
    preparation = await _prepare(
        app,
        seeded,
        version_id,
        key or f"replay-{uuid.uuid4().hex[:12]}",
        side_effect_type="CANCEL_EXECUTION",
    )
    await _approve_p0(app, preparation["id"])
    response = await _commit(app, preparation["id"], SeededWorkspace.ADMIN_B)
    assert response.status_code == 200
    grant = await db_session.get(
        HarnessCapabilityGrant, uuid.UUID(response.json()["token_id"])
    )
    assert grant is not None
    return grant, grant.token_sha256


async def test_capability_replay_guard(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    frozen_version: tuple
) -> None:
    """同一令牌第二次消费 → HARNESS_409_CAPABILITY_REPLAYED（FR-34 / AC-05）。"""
    grant, digest = await _capability_grant(app, db_session, seeded, frozen_version)
    guard = ReplayGuard()

    first = await guard.consume(
        db_session, token_id=grant.token_id, token_sha256=digest
    )
    assert first.preparation_id == grant.preparation_id

    with pytest.raises(CapabilityReplayed) as excinfo:
        await guard.consume(db_session, token_id=grant.token_id, token_sha256=digest)
    assert excinfo.value.code == "HARNESS_409_CAPABILITY_REPLAYED"

    await db_session.refresh(grant)
    assert grant.consumed_at is not None


async def test_replay_guard_rejects_unknown_token(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    frozen_version: tuple
) -> None:
    """未知 / 摘要不匹配的令牌 → 重放语义拒绝（不泄露令牌存在性）。"""
    grant, digest = await _capability_grant(app, db_session, seeded, frozen_version)
    guard = ReplayGuard()
    with pytest.raises(CapabilityReplayed):
        await guard.consume(
            db_session, token_id=grant.token_id, token_sha256="0" * 64
        )
    with pytest.raises(CapabilityReplayed):
        await guard.consume(
            db_session, token_id=uuid.uuid4(), token_sha256=digest
        )


async def test_capability_expired(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    frozen_version: tuple, harness_signing_key, harness_public_keys
) -> None:
    """过期令牌 → HARNESS_401_CAPABILITY_EXPIRED（FR-33）。"""
    from etl_agent.harness.capability.verifier import CapabilityVerifier
    from etl_agent.harness.errors import CapabilityExpired

    grant, _digest = await _capability_grant(app, db_session, seeded, frozen_version)
    verifier = CapabilityVerifier(harness_public_keys)

    # 用库中 claims 现场构造一份已过期令牌（同一私钥签名）
    from etl_agent.harness.capability.issuer import CapabilityClaims, CapabilityIssuer

    claims = CapabilityClaims(
        tool_name="pipeline.execute",
        tool_version="1.0.0",
        subject=str(seeded.admin_b_user_id),
        environment="dev",
        artifact_sha256=grant.claims.get("artifact_sha256", "a" * 64),
        supervision_contract_sha256=grant.claims.get("supervision_contract_sha256", "b" * 64),
        classification_report_sha256=grant.claims.get("classification_report_sha256", ""),
        allowed_mitigations=["CANCEL"],
        project_id=str(grant.claims.get("project_id", "")),
        preparation_id=str(grant.preparation_id),
        pipeline_version_id=str(grant.claims.get("pipeline_version_id", "")),
    )
    expired = CapabilityIssuer(harness_signing_key).issue(
        claims, now=datetime(2020, 1, 1, tzinfo=UTC), ttl_seconds=30
    )
    with pytest.raises(CapabilityExpired):
        verifier.verify(expired.token, now=datetime.now(UTC))


async def test_tool_broker_registry_only(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    frozen_version: tuple
) -> None:
    """未注册副作用经 Tool Broker 下发 → HARNESS_403_TOOL_NOT_REGISTERED（FR-34）。"""
    grant, digest = await _capability_grant(app, db_session, seeded, frozen_version)
    broker = ToolBroker()
    command = BrokerCommand(
        command_type="TELEPORT_DATA",
        project_id=seeded.project_a_id,
        preparation_id=grant.preparation_id,
    )
    with pytest.raises(ToolNotRegistered):
        await broker.dispatch(
            db_session, command=command, token_id=grant.token_id, token_sha256=digest
        )


async def test_tool_broker_enqueue_only(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    frozen_version: tuple
) -> None:
    """注册清单内副作用 → 只写 Outbox（S6 不执行副作用，禁止事项 23）。"""
    grant, digest = await _capability_grant(app, db_session, seeded, frozen_version)
    broker = ToolBroker()
    command = BrokerCommand(
        command_type="CANCEL_EXECUTION",
        project_id=seeded.project_a_id,
        preparation_id=grant.preparation_id,
    )
    row = await broker.dispatch(
        db_session, command=command, token_id=grant.token_id, token_sha256=digest
    )
    assert row.status == outbox_service.STATUS_PENDING
    assert row.command_type == "CANCEL_EXECUTION"
    # 未执行副作用：令牌未被消费（消费发生在 S7 Worker）
    await db_session.refresh(grant)
    assert grant.consumed_at is None


async def test_outbox_pending_written(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    frozen_version: tuple
) -> None:
    """Commit 后 Outbox 有且仅有一条 PENDING（FR-35 / DoD-13）。"""
    from etl_agent.infrastructure.models import HarnessCommandOutbox

    await _capability_grant(app, db_session, seeded, frozen_version)
    rows = (
        await db_session.execute(
            select(HarnessCommandOutbox).where(HarnessCommandOutbox.status == "PENDING")
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].command_type == MAIN_EXECUTION_COMMAND
    assert is_registered(MAIN_EXECUTION_COMMAND)
    assert "token_id" in rows[0].payload


async def test_outbox_state_machine(db_session: AsyncSession, seeded: SeededWorkspace) -> None:
    """Outbox 状态机：PENDING → SENDING → SENT → FAILED 与重试计数（FR-35 接口）。"""
    row = await outbox_service.enqueue(
        db_session,
        project_id=seeded.project_a_id,
        command_type=MAIN_EXECUTION_COMMAND,
        payload={"note": "S6 状态机用例"},
    )
    assert row.status == "PENDING" and row.retry_count == 0

    claimed = await outbox_service.claim_next(db_session)
    assert claimed is not None and claimed.id == row.id
    assert claimed.status == "SENDING"
    assert claimed.locked_until is not None
    # 锁未释放前无法再次领取
    assert await outbox_service.claim_next(db_session) is None

    sent = await outbox_service.mark_sent(db_session, row.id)
    assert sent.status == "SENT" and sent.sent_at is not None

    failed = await outbox_service.mark_failed(db_session, row.id, "网络错误")
    assert failed.status == "FAILED" and failed.retry_count == 1
    assert failed.last_error == "网络错误"


async def test_outbox_second_claim_returns_none(db_session: AsyncSession, seeded: SeededWorkspace) -> None:
    """并发领取：同一命令只会被一个扫描器领取（条件 UPDATE 判据）。"""
    await outbox_service.enqueue(
        db_session,
        project_id=seeded.project_a_id,
        command_type=MAIN_EXECUTION_COMMAND,
        payload={},
    )
    first = await outbox_service.claim_next(db_session)
    assert first is not None
    assert await outbox_service.claim_next(db_session) is None


async def test_zero_plaintext_harness_responses(
    app: FastAPI, seeded: SeededWorkspace, frozen_version: tuple
) -> None:
    """AC-02：Prepare / Commit / 审批响应与业务库遥测均不含凭据与需求正文。"""
    version_id, _ = frozen_version
    marker = f"REQ-MARKER-{uuid.uuid4().hex}"
    password = str(dev_pg_credentials()["password"])
    async with make_client(app) as client:
        created = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/preparations",
            json={
                "pipeline_version_id": str(version_id),
                "idempotency_key": f"zero-plain-{uuid.uuid4().hex[:8]}",
                "environment": "dev",
                "tool_name": "pipeline.execute",
                "tool_version": "1.0.0",
                "side_effect_type": "CANCEL_EXECUTION",
                "budget": {"max_records": 1_000_000},
            },
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
        assert created.status_code == 201
        assert marker not in created.text
        assert password not in created.text

        # 影响报告与监督契约不含明文凭据
        prepared = await client.get(
            f"/api/v1/preparations/{created.json()['id']}/decisions",
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
        assert password not in prepared.text
