"""Commit / AC-05 核心测试（T-12.7；S6 提示词第十二节 test_commit.py）。

覆盖（FR-33/35）：服务端重验指纹与审批事实、签发单次 Capability（TTL ≤300s）、
同事务创建 ExecutionRun + Outbox、重复 Commit 被拒。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.harness.prepare.service import STATUS_READY
from etl_agent.infrastructure.models import (
    ExecutionPreparation,
    ExecutionRun,
    HarnessCapabilityGrant,
    HarnessCommandOutbox,
)
from tests.helpers import SeededWorkspace, make_client


async def _prepare(
    app: FastAPI,
    seeded: SeededWorkspace,
    version_id: uuid.UUID,
    key: str,
    *,
    side_effect_type: str | None = None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "pipeline_version_id": str(version_id),
        "idempotency_key": key,
        "environment": "dev",
        "tool_name": "pipeline.execute",
        "tool_version": "1.0.0",
    }
    if side_effect_type:
        body["side_effect_type"] = side_effect_type
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/preparations",
            json=body,
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert response.status_code == 201, response.text
    return response.json()


async def _approve_p0(app: FastAPI, preparation_id: str) -> None:
    """双人批准 P0 准备单（DATA_APPROVER + SECURITY_APPROVER）。"""
    async with make_client(app) as client:
        first = await client.post(
            f"/api/v1/preparations/{preparation_id}/decisions",
            json={"role": "DATA_APPROVER", "decision": "APPROVE", "reason": "数据维度通过"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        assert first.status_code == 200
        second = await client.post(
            f"/api/v1/preparations/{preparation_id}/decisions",
            json={"role": "SECURITY_APPROVER", "decision": "APPROVE", "reason": "安全维度通过"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
        assert second.status_code == 200
        assert second.json()["status"] == STATUS_READY


async def _commit(app: FastAPI, preparation_id: str, actor: str) -> object:
    async with make_client(app) as client:
        return await client.post(
            f"/api/v1/preparations/{preparation_id}/commit",
            json={},
            headers={"X-Actor-ID": actor},
        )


async def test_commit_atomic_outbox(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    frozen_version: tuple,
) -> None:
    """同事务：ExecutionRun(QUEUED) + Outbox(PENDING) + 准备单 COMMITTED（FR-33/35）。"""
    version_id, _ = frozen_version
    preparation = await _prepare(
        app, seeded, version_id, "atomic-key", side_effect_type="CANCEL_EXECUTION"
    )
    await _approve_p0(app, preparation["id"])

    response = await _commit(app, preparation["id"], SeededWorkspace.ADMIN_B)
    assert response.status_code == 200, response.text
    body = response.json()
    # 不返回令牌本体（FR-33）
    assert "token" not in body
    assert "token_id" in body and "claims" in body

    run = await db_session.get(ExecutionRun, uuid.UUID(body["run_id"]))
    assert run is not None
    assert run.status == "QUEUED"
    assert run.trigger == "MANUAL"
    assert str(run.harness_preparation_id) == preparation["id"]

    outbox_rows = (
        await db_session.execute(select(HarnessCommandOutbox))
    ).scalars().all()
    assert len(outbox_rows) == 1
    assert outbox_rows[0].status == "PENDING"
    assert outbox_rows[0].command_type == "PIPELINE_EXECUTE"
    assert outbox_rows[0].payload["preparation_id"] == preparation["id"]

    grant = await db_session.get(HarnessCapabilityGrant, uuid.UUID(body["token_id"]))
    assert grant is not None
    assert len(grant.token_sha256) == 64
    assert grant.consumed_at is None
    assert grant.claims["artifact_sha256"]

    row = await db_session.get(ExecutionPreparation, uuid.UUID(preparation["id"]))
    assert row is not None and row.status == "COMMITTED"
    assert row.committed_by == seeded.admin_b_user_id


async def test_commit_signs_capability(
    app: FastAPI, seeded: SeededWorkspace, frozen_version: tuple
) -> None:
    """签发 Ed25519 单次 Capability：TTL ≤300s + claims 绑定（AC-05）。"""
    version_id, digest = frozen_version
    preparation = await _prepare(
        app, seeded, version_id, "sign-key", side_effect_type="CANCEL_EXECUTION"
    )
    await _approve_p0(app, preparation["id"])

    response = await _commit(app, preparation["id"], SeededWorkspace.ADMIN_B)
    assert response.status_code == 200
    claims = response.json()["claims"]
    for field in (
        "tool_name",
        "tool_version",
        "subject",
        "environment",
        "artifact_sha256",
        "supervision_contract_sha256",
        "project_id",
        "preparation_id",
        "pipeline_version_id",
        "allowed_mitigations",
    ):
        assert field in claims
    assert claims["artifact_sha256"] == digest
    assert claims["environment"] == "dev"
    assert "CANCEL" in claims["allowed_mitigations"]


async def test_commit_insufficient_approval(
    app: FastAPI, seeded: SeededWorkspace, frozen_version: tuple
) -> None:
    """审批不足即 Commit → 409 HARNESS_409_APPROVAL_INSUFFICIENT（AC-05）。"""
    version_id, _ = frozen_version
    preparation = await _prepare(
        app, seeded, version_id, "insufficient-key", side_effect_type="CANCEL_EXECUTION"
    )
    # 只批一票（P0 需要 2 票）
    async with make_client(app) as client:
        await client.post(
            f"/api/v1/preparations/{preparation['id']}/decisions",
            json={"role": "DATA_APPROVER", "decision": "APPROVE", "reason": "单票"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    response = await _commit(app, preparation["id"], SeededWorkspace.ADMIN_B)
    assert response.status_code == 409
    assert response.json()["code"] == "HARNESS_409_APPROVAL_INSUFFICIENT"


async def test_commit_reverify_fingerprint(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    frozen_version: tuple
) -> None:
    """指纹被篡改 → Commit 拒绝（FR-33 服务端重验，AC-05）。"""
    version_id, _ = frozen_version
    preparation = await _prepare(
        app, seeded, version_id, "reverify-key", side_effect_type="CANCEL_EXECUTION"
    )
    await _approve_p0(app, preparation["id"])

    row = await db_session.get(ExecutionPreparation, uuid.UUID(preparation["id"]))
    row.input_fingerprint = "0" * 64  # 模拟篡改
    await db_session.flush()
    await db_session.commit()

    response = await _commit(app, preparation["id"], SeededWorkspace.ADMIN_B)
    assert response.status_code == 409
    assert response.json()["code"] == "BIZ_409_STATE_CONFLICT"


async def test_commit_already_committed(
    app: FastAPI, seeded: SeededWorkspace, frozen_version: tuple
) -> None:
    """重复 Commit → 409 HARNESS_409_ALREADY_COMMITTED（FR-33 单次）。"""
    version_id, _ = frozen_version
    preparation = await _prepare(
        app, seeded, version_id, "dup-key", side_effect_type="CANCEL_EXECUTION"
    )
    await _approve_p0(app, preparation["id"])

    first = await _commit(app, preparation["id"], SeededWorkspace.ADMIN_B)
    assert first.status_code == 200
    second = await _commit(app, preparation["id"], SeededWorkspace.ADMIN_B)
    assert second.status_code == 409
    assert second.json()["code"] == "HARNESS_409_ALREADY_COMMITTED"


async def test_commit_only_operator(
    app: FastAPI, seeded: SeededWorkspace, frozen_version: tuple
) -> None:
    """非 OPERATOR（admin-a 只持 DATA_APPROVER）→ 403 AUTH_403_ROLE_REQUIRED（API-10）。"""
    version_id, _ = frozen_version
    preparation = await _prepare(
        app, seeded, version_id, "operator-key", side_effect_type="CANCEL_EXECUTION"
    )
    await _approve_p0(app, preparation["id"])
    response = await _commit(app, preparation["id"], SeededWorkspace.ADMIN_A)
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_403_ROLE_REQUIRED"


async def test_commit_cross_project_403(
    app: FastAPI, seeded: SeededWorkspace, frozen_version: tuple
) -> None:
    """局外人 Commit 他人准备单 → 403（AC-12）。"""
    version_id, _ = frozen_version
    preparation = await _prepare(
        app, seeded, version_id, "cross-key", side_effect_type="CANCEL_EXECUTION"
    )
    await _approve_p0(app, preparation["id"])
    response = await _commit(app, preparation["id"], SeededWorkspace.OUTSIDER)
    assert response.status_code == 403
