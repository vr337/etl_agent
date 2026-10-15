"""四眼职责分离测试（T-12.6；S6 提示词第十二节 test_four_eyes.py，AC-01 核心）。

覆盖（AC-01 / PRD 2.3）：
1. 申请人自批 → `HARNESS_409_SELF_APPROVAL`；
2. 同角色二次审批 → `HARNESS_409_ROLE_TAKEN`；
3. 同一自然人占两个槽 → `HARNESS_409_SELF_APPROVAL`（一人一槽）；
4. P0 场景 `required_approvals=2`，一人通过不足以 READY；
5. 无 Harness 职责 → `AUTH_403_ROLE_REQUIRED`；
6. DB 双唯一约束兜底。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.harness.prepare.service import STATUS_READY, STATUS_WAITING_APPROVAL
from etl_agent.infrastructure.models import (
    HarnessApprovalDecision,
)
from tests.helpers import SeededWorkspace, make_client


async def _prepare_p0(
    app: FastAPI, seeded: SeededWorkspace, frozen_version: tuple, key: str = "p0-key"
) -> str:
    """创建 P0 准备单（破坏性副作用 → 双审批槽）。"""
    version_id, _ = frozen_version
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/projects/{seeded.project_a_id}/preparations",
            json={
                "pipeline_version_id": str(version_id),
                "idempotency_key": key,
                "environment": "dev",
                "tool_name": "pipeline.execute",
                "tool_version": "1.0.0",
                "side_effect_type": "CANCEL_EXECUTION",
            },
            headers={"X-Actor-ID": SeededWorkspace.ENGINEER_A},
        )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == STATUS_WAITING_APPROVAL
    assert body["required_approvals"] == 2
    return body["id"]


async def _decide(
    app: FastAPI,
    preparation_id: str,
    actor: str,
    role: str = "DATA_APPROVER",
    decision: str = "APPROVE",
) -> object:
    async with make_client(app) as client:
        return await client.post(
            f"/api/v1/preparations/{preparation_id}/decisions",
            json={"role": role, "decision": decision, "reason": "测试审批"},
            headers={"X-Actor-ID": actor},
        )


async def test_four_eyes_self_approval(
    app: FastAPI, seeded: SeededWorkspace, frozen_version: tuple
) -> None:
    """申请人自批 → 409 HARNESS_409_SELF_APPROVAL（AC-01 红线）。"""
    preparation_id = await _prepare_p0(app, seeded, frozen_version, "self-key")
    response = await _decide(app, preparation_id, SeededWorkspace.ENGINEER_A)
    assert response.status_code == 409
    assert response.json()["code"] == "HARNESS_409_SELF_APPROVAL"


async def test_four_eyes_role_taken(
    app: FastAPI, seeded: SeededWorkspace, frozen_version: tuple
) -> None:
    """同职责槽二次审批 → 409 HARNESS_409_ROLE_TAKEN（PRD 2.3-2）。

    注意语义顺序：engineer-a 既是申请人又持 DATA_APPROVER，他去批会**先**命中自批检查
    （PRD 2.3-1 优先于 2.3-2），所以这里用非申请人的 admin-a 占同一槽来验证 ROLE_TAKEN。
    """
    preparation_id = await _prepare_p0(app, seeded, frozen_version, "role-key")
    second = await _decide(app, preparation_id, SeededWorkspace.ADMIN_A)
    assert second.status_code == 200

    # admin-a 已占 DATA_APPROVER 槽；outsider 是**非申请人**的第三位 DATA_APPROVER，
    # 再占同一槽 → 职责槽被占（PRD 2.3-2）
    third = await _decide(app, preparation_id, SeededWorkspace.OUTSIDER)
    assert third.status_code == 409
    assert third.json()["code"] == "HARNESS_409_ROLE_TAKEN"


async def test_four_eyes_one_person_one_slot(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    frozen_version: tuple,
) -> None:
    """同一自然人不得占两个审批槽 → HARNESS_409_SELF_APPROVAL（PRD 2.3-1）。

    admin-b 同时持 SECURITY_APPROVER 与（本例临时授予的）DATA_APPROVER：
    先占安全槽，再占数据槽即触发「一人一槽」。
    """
    from etl_agent.infrastructure.models import ProjectRoleGrant

    db_session.add(
        ProjectRoleGrant(
            project_id=seeded.project_a_id,
            user_id=seeded.admin_b_user_id,
            role="DATA_APPROVER",
            granted_by=seeded.admin_a_user_id,
        )
    )
    await db_session.flush()

    preparation_id = await _prepare_p0(app, seeded, frozen_version, "oneslot-key")
    first = await _decide(
        app, preparation_id, SeededWorkspace.ADMIN_B, role="SECURITY_APPROVER"
    )
    assert first.status_code == 200
    second = await _decide(
        app, preparation_id, SeededWorkspace.ADMIN_B, role="DATA_APPROVER"
    )
    assert second.status_code == 409
    assert second.json()["code"] == "HARNESS_409_SELF_APPROVAL"


async def test_four_eyes_p0_requires_two(
    app: FastAPI, seeded: SeededWorkspace, frozen_version: tuple
) -> None:
    """P0 场景 required_approvals=2：一人通过不足以 READY，双人才 READY（AC-01）。"""
    preparation_id = await _prepare_p0(app, seeded, frozen_version, "two-key")

    data_only = await _decide(app, preparation_id, SeededWorkspace.ADMIN_A)
    assert data_only.status_code == 200
    assert data_only.json()["approvals_received"] == 1
    assert data_only.json()["status"] == STATUS_WAITING_APPROVAL

    both = await _decide(
        app, preparation_id, SeededWorkspace.ADMIN_B, role="SECURITY_APPROVER"
    )
    assert both.status_code == 200
    assert both.json()["approvals_received"] == 2
    assert both.json()["status"] == STATUS_READY


async def test_four_eyes_role_required(
    app: FastAPI, seeded: SeededWorkspace, frozen_version: tuple
) -> None:
    """无该 Harness 职责的成员审批 → 403 AUTH_403_ROLE_REQUIRED（FR-03）。"""
    preparation_id = await _prepare_p0(app, seeded, frozen_version, "role-req-key")
    # admin-a 只持 DATA_APPROVER，去批 SECURITY_APPROVER 槽
    response = await _decide(
        app, preparation_id, SeededWorkspace.ADMIN_A, role="SECURITY_APPROVER"
    )
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_403_ROLE_REQUIRED"


async def test_approval_evidence_sha256(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    frozen_version: tuple
) -> None:
    """决策带 evidence_sha256 落库（FR-32）。"""
    preparation_id = await _prepare_p0(app, seeded, frozen_version, "evidence-key")
    evidence = "d" * 64
    async with make_client(app) as client:
        response = await client.post(
            f"/api/v1/preparations/{preparation_id}/decisions",
            json={
                "role": "DATA_APPROVER",
                "decision": "APPROVE",
                "reason": "已核对 Schema",
                "evidence_sha256": evidence,
            },
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 200

    rows = (
        await db_session.execute(
            select(HarnessApprovalDecision).where(
                HarnessApprovalDecision.preparation_id == uuid.UUID(preparation_id)
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].evidence_sha256 == evidence
    assert rows[0].reason == "已核对 Schema"


async def test_four_eyes_reject_blocks_progress(
    app: FastAPI, seeded: SeededWorkspace, frozen_version: tuple
) -> None:
    """REJECT 后准备单转 REJECTED，后续审批被拒绝（FR-32）。"""
    preparation_id = await _prepare_p0(app, seeded, frozen_version, "reject-key")
    rejected = await _decide(
        app, preparation_id, SeededWorkspace.ADMIN_A, decision="REJECT"
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"

    follow_up = await _decide(
        app, preparation_id, SeededWorkspace.ADMIN_B, role="SECURITY_APPROVER"
    )
    assert follow_up.status_code == 409
    assert follow_up.json()["code"] == "BIZ_409_STATE_CONFLICT"


async def test_db_double_unique_constraint(
    db_session: AsyncSession, seeded: SeededWorkspace, frozen_version: tuple
) -> None:
    """DB 双唯一：直接插两行同 (preparation_id, role) / (preparation_id, decided_by) → IntegrityError。"""
    from etl_agent.harness.pdp.engine import PdpEngine
    from etl_agent.harness.prepare.service import PrepareInput, PrepareService
    from tests.harness.conftest import build_memory_checkpointer  # noqa: F401

    version_id, _ = frozen_version
    service = PrepareService(PdpEngine(_package()))
    preparation, _ = await service.create_preparation(
        db_session,
        project_id=seeded.project_a_id,
        payload=PrepareInput(
            pipeline_version_id=version_id,
            idempotency_key="db-unique-key",
            environment="dev",
            tool_name="pipeline.execute",
            tool_version="1.0.0",
            side_effect_type="CANCEL_EXECUTION",
        ),
        requested_by=seeded.engineer_a_user_id,
    )
    await db_session.flush()

    decision = HarnessApprovalDecision(
        preparation_id=preparation.id,
        role="DATA_APPROVER",
        decided_by=seeded.admin_a_user_id,
        decision="APPROVE",
    )
    db_session.add(decision)
    await db_session.flush()
    rows = (
        await db_session.execute(
            select(HarnessApprovalDecision).where(
                HarnessApprovalDecision.preparation_id == preparation.id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].role == "DATA_APPROVER"

    # 同槽第二位审批人 → 违反 (preparation_id, role) 唯一
    with pytest.raises(IntegrityError):
        duplicate_role = HarnessApprovalDecision(
            preparation_id=preparation.id,
            role="DATA_APPROVER",
            decided_by=seeded.admin_b_user_id,
            decision="APPROVE",
        )
        db_session.add(duplicate_role)
        await db_session.flush()
    await db_session.rollback()

    # 同一自然人占第二槽 → 违反 (preparation_id, decided_by) 唯一
    with pytest.raises(IntegrityError):
        duplicate_person = HarnessApprovalDecision(
            preparation_id=preparation.id,
            role="SECURITY_APPROVER",
            decided_by=seeded.admin_a_user_id,
            decision="APPROVE",
        )
        db_session.add(duplicate_person)
        await db_session.flush()


def _package():
    from etl_agent.config import settings
    from etl_agent.harness.policy.package import load_package

    return load_package(
        settings.policy_package_path,
        settings.policy_package_signature_path,
        settings.harness_public_key_list,
    )
