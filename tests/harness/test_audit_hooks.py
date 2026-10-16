"""F3 审计钩子接线回归（钩子 1~3：PDP 决策 / Prepare 创建 / 审批 / Commit）。

背景：S9 的 `audit/hooks.py` 七个 `on_*` 钩子曾在 src/ 全域**零调用点**（死代码），
证据账本恒空、`evidence-stream/verify` 恒 404（T-AUDIT-01 已登记，本任务接线闭环）。
本组用例守接线事实：治理路径走完后 `harness_evidence_events` 必须有对应类型行。

提交语义（F3 接线原则）：钩子内只 flush、不自行 commit，证据随调用方既有事务落库
——本组经路由走完整请求周期，断言即「commit 后存在」的生产语义；DENY 路径按
人类裁决补充 1：raise 前由 service 单独提交决策行 + 证据（否则 403 回滚把留痕
一起冲掉），单测见 `test_deny_evidence_survives_403`。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.harness.errors import PolicyDeny
from etl_agent.harness.pdp.engine import PdpEngine
from etl_agent.harness.prepare.service import PrepareInput, PrepareService
from etl_agent.infrastructure.models import HarnessEvidenceEvent, HarnessEvidenceStream
from tests.helpers import SeededWorkspace, make_client


async def _evidence_event_types(db_session: AsyncSession, project_id) -> set[str]:
    """项目下证据事件类型集合（按项目过滤：流按项目唯一，全表查询会被
    其他工作区/历史数据污染——联调 dev 库常态）。"""
    rows = (
        (
            await db_session.execute(
                select(HarnessEvidenceEvent.event_type)
                .join(HarnessEvidenceStream, HarnessEvidenceStream.id == HarnessEvidenceEvent.stream_id)
                .where(HarnessEvidenceStream.project_id == project_id)
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


async def _stream_count(db_session: AsyncSession, project_id) -> int:
    return len(
        (
            await db_session.execute(
                select(HarnessEvidenceStream).where(HarnessEvidenceStream.project_id == project_id)
            )
        )
        .scalars()
        .all()
    )


async def _prepare_p0(
    app: FastAPI, seeded: SeededWorkspace, frozen_version: tuple, key: str
) -> str:
    """创建 P0 准备单（破坏性副作用 → 双审批槽，WAITING_APPROVAL）。"""
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
    return response.json()["id"]


async def test_prepare_emits_pdp_and_prepare_evidence(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    frozen_version: tuple,
) -> None:
    """钩子 1+2：创建准备单 → PDP_DECISION 与 PREPARE_CREATED 双写证据账本。"""
    await _prepare_p0(app, seeded, frozen_version, "hook-prep-001")

    types = await _evidence_event_types(db_session, seeded.project_a_id)
    assert "PDP_DECISION" in types
    assert "PREPARE_CREATED" in types
    # 证据流按项目唯一：一次创建只开一条流（get_or_create_stream 语义）
    assert await _stream_count(db_session, seeded.project_a_id) == 1


async def test_deny_evidence_survives_403(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    frozen_version: tuple,
) -> None:
    """钩子 1（DENY 路径，人类裁决补充 1）：403 后 PDP_DECISION 证据必须仍在。

    PolicyDeny 由路由转 403，请求会话随后关闭——若证据只 flush 不 commit，
    留痕会随事务回滚丢失（「为什么被拒」无从复现，FR-29）。接线在 service 内
    raise 前单独提交决策行 + 证据，本用例守该语义。
    """
    from etl_agent.config import settings
    from etl_agent.harness.policy.package import load_package

    version_id, _ = frozen_version
    package = load_package(
        settings.policy_package_path,
        settings.policy_package_signature_path,
        settings.harness_public_key_list,
    )
    service = PrepareService(PdpEngine(package))
    with pytest.raises(PolicyDeny):
        await service.create_preparation(
            db_session,
            project_id=seeded.project_a_id,
            payload=PrepareInput(
                pipeline_version_id=version_id,
                idempotency_key=f"deny-{uuid.uuid4().hex[:8]}",
                environment="dev",
                tool_name="pipeline.execute",
                tool_version="1.0.0",
                # 跨项目工具意图 → DENY（无放行通道）
                actor_project_id=seeded.project_b_id,
            ),
            requested_by=seeded.engineer_a_user_id,
        )

    types = await _evidence_event_types(db_session, seeded.project_a_id)
    assert "PDP_DECISION" in types


async def test_approval_and_commit_emit_evidence(
    app: FastAPI,
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    frozen_version: tuple,
) -> None:
    """钩子 3+4：双槽审批 + Commit → APPROVAL_DECISION×2 与 COMMIT_ISSUED 双写。"""
    preparation_id = await _prepare_p0(app, seeded, frozen_version, "hook-approve-001")
    async with make_client(app) as client:
        first = await client.post(
            f"/api/v1/preparations/{preparation_id}/decisions",
            json={"role": "DATA_APPROVER", "decision": "APPROVE", "reason": "数据口径已核对"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
        assert first.status_code == 200, first.text
        second = await client.post(
            f"/api/v1/preparations/{preparation_id}/decisions",
            json={"role": "SECURITY_APPROVER", "decision": "APPROVE", "reason": "安全边界已核对"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
        assert second.status_code == 200, second.text
        # Commit：OPERATOR 由 harness_roles 夹具授予 admin-b（成员 + 职责两步齐备）
        committed = await client.post(
            f"/api/v1/preparations/{preparation_id}/commit",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_B},
        )
        assert committed.status_code in (200, 201), committed.text

    types = await _evidence_event_types(db_session, seeded.project_a_id)
    assert "APPROVAL_DECISION" in types
    assert "COMMIT_ISSUED" in types
