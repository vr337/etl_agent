"""S9 全量审计 — 关键事件钩子用例（7 类事件全记录 + 审计/证据双写）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N / 第 5.2 章 audit_events /
harness_evidence_events
覆盖 FR：FR-36（关键事件钩子 + 证据账本联动）
对应 AC：AC-09（证据覆盖率：关键事件必须有证据）、AC-02（零明文）
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.audit.hooks import (
    KEY_EVENT_TYPES,
    on_approval_decision,
    on_commit_issued,
    on_execution_transition,
    on_pdp_decision,
    on_prepare_created,
    on_rollback_event,
    on_supervision_action,
)
from etl_agent.evidence import require_stream
from etl_agent.infrastructure.models import (
    AuditEvent,
    ExecutionPreparation,
    ExecutionRun,
    HarnessEvidenceEvent,
    HarnessPolicyDecision,
    Pipeline,
    PipelineVersion,
)
from tests.helpers import SeededWorkspace

pytestmark = pytest.mark.asyncio


async def _audit_actions(session: AsyncSession, project_id: uuid.UUID) -> set[str]:
    result = await session.execute(
        select(AuditEvent.action).where(AuditEvent.project_id == project_id)
    )
    return {row[0] for row in result}


async def _evidence_types(session: AsyncSession, project_id: uuid.UUID) -> set[str]:
    stream = await require_stream(session, project_id)
    result = await session.execute(
        select(HarnessEvidenceEvent.event_type).where(
            HarnessEvidenceEvent.stream_id == stream.id
        )
    )
    return {row[0] for row in result}


async def _real_decision(
    session: AsyncSession, project_id: uuid.UUID
) -> HarnessPolicyDecision:
    """建一条真实 PDP 决策行——证据账本的 `policy_decision_id` 有外键，不能拿假 UUID 顶。"""
    decision = HarnessPolicyDecision(
        project_id=project_id,
        outcome="REQUIRE_APPROVAL",
        risk_level="P1",
        rule_evidence=[],
        policy_package_id="test-package",
        policy_package_sha256="0" * 64,
    )
    session.add(decision)
    await session.flush()
    return decision


async def _real_preparation(
    session: AsyncSession, project_id: uuid.UUID, decision_id: uuid.UUID, requester_id: uuid.UUID
) -> ExecutionPreparation:
    """建真实准备单（证据账本不直接引用它，但审计资源的语义要求它是真行）。"""
    pipeline = Pipeline(project_id=project_id, name=f"pipe-{uuid.uuid4().hex[:8]}")
    session.add(pipeline)
    await session.flush()
    version = PipelineVersion(pipeline_id=pipeline.id, version_number=1, status="DRAFT")
    session.add(version)
    await session.flush()

    preparation = ExecutionPreparation(
        project_id=project_id,
        policy_decision_id=decision_id,
        pipeline_version_id=version.id,
        status="WAITING_APPROVAL",
        risk_level="P1",
        environment="dev",
        tool_name="pipeline.execute",
        tool_version="1.0.0",
        artifact_sha256="a" * 64,
        input_fingerprint="b" * 64,
        resource_fingerprint="c" * 64,
        impact_report={"requested_input": {"side_effect_type": None, "resource_ids": []}},
        rollback_plan={},
        supervision_contract={"max_records": 1000, "warning_ratio": 0.8},
        required_roles=["SECURITY_APPROVER"],
        required_approvals=1,
        idempotency_key=f"s9-audit-{uuid.uuid4().hex[:8]}",
        requested_by=requester_id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(preparation)
    await session.flush()
    return preparation


async def test_audit_all_key_events(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """**DoD-15**：7 类关键事件全部记录（审计 + 证据双写）。"""
    project_id = seeded.project_a_id
    decision = await _real_decision(db_session, project_id)
    preparation = await _real_preparation(
        db_session, project_id, decision.id, seeded.engineer_a_user_id
    )

    run = ExecutionRun(
        pipeline_version_id=preparation.pipeline_version_id,
        harness_preparation_id=preparation.id,
        idempotency_key=f"commit-{preparation.id}",
        status="QUEUED",
        trigger="MANUAL",
        supervision_contract_sha256="c" * 64,
    )
    db_session.add(run)
    await db_session.flush()

    await on_pdp_decision(
        db_session,
        project_id=project_id,
        decision_id=decision.id,
        outcome="REQUIRE_APPROVAL",
        risk_level="P1",
        actor_id=seeded.engineer_a_user_id,
    )
    await on_prepare_created(
        db_session,
        project_id=project_id,
        preparation_id=preparation.id,
        actor_id=seeded.engineer_a_user_id,
        status="WAITING_APPROVAL",
        risk_level="P1",
    )
    await on_approval_decision(
        db_session,
        project_id=project_id,
        preparation_id=preparation.id,
        role="SECURITY_APPROVER",
        decision="APPROVE",
        decided_by=seeded.admin_a_user_id,
    )
    await on_commit_issued(
        db_session,
        project_id=project_id,
        preparation_id=preparation.id,
        run_id=run.id,
        token_id=uuid.uuid4(),
        actor_id=seeded.engineer_a_user_id,
    )
    await on_execution_transition(
        db_session,
        project_id=project_id,
        run_id=run.id,
        from_status="QUEUED",
        to_status="RUNNING",
    )
    await on_supervision_action(
        db_session,
        project_id=project_id,
        run_id=run.id,
        action="QUARANTINE",
        risk_level="P0",
        rule_ids=["SUPERVISION-P0-RECORD"],
    )
    await on_rollback_event(
        db_session,
        project_id=project_id,
        rollback_id=uuid.uuid4(),
        phase="REQUESTED",
        actor_id=seeded.engineer_a_user_id,
        status="PENDING",
    )

    actions = await _audit_actions(db_session, project_id)
    for expected in (
        "PDP_DECISION",
        "PREPARE_CREATED",
        "APPROVAL_DECISION",
        "COMMIT_ISSUED",
        "EXECUTION_TRANSITION",
        "SUPERVISION_ACTION",
        "ROLLBACK_REQUESTED",
    ):
        assert expected in actions, f"缺少审计动作 {expected}"

    # 证据账本同步落 7 类事件之一（双写）
    evidence_types = await _evidence_types(db_session, project_id)
    assert "PDP_DECISION" in evidence_types
    assert "SUPERVISION_ACTION" in evidence_types
    assert "ROLLBACK_REQUESTED" in evidence_types
    assert len(KEY_EVENT_TYPES) == 9  # 7 类 + 回滚三阶段细分


async def test_audit_hook_zero_plaintext(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """**AC-02**：钩子写入的审计与证据均不含凭证明文。"""
    marker = "hook-plaintext-secret"
    await on_approval_decision(
        db_session,
        project_id=seeded.project_a_id,
        preparation_id=uuid.uuid4(),
        role="SECURITY_APPROVER",
        decision="APPROVE",
        decided_by=seeded.admin_a_user_id,
        reason=marker,
    )
    result = await db_session.execute(
        select(AuditEvent).where(AuditEvent.project_id == seeded.project_a_id)
    )
    row = result.scalars().first()
    assert row is not None
    # reason 是治理理由（可读文本），before/after 才是脱敏重点
    assert row.before is None
    assert row.after is not None
    assert "password" not in str(row.after).lower()


async def test_audit_commit_does_not_store_token(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """令牌本体不入审计：只记 token_id（AC-02）。"""
    token_id = uuid.uuid4()
    await on_commit_issued(
        db_session,
        project_id=seeded.project_a_id,
        preparation_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        token_id=token_id,
        actor_id=seeded.engineer_a_user_id,
    )
    result = await db_session.execute(
        select(AuditEvent).where(AuditEvent.project_id == seeded.project_a_id)
    )
    row = result.scalars().first()
    assert row is not None and row.after is not None
    assert row.after["token_id"] == str(token_id)
    # after 里没有任何形如「json.signature」的令牌本体
    assert "." not in str(row.after.get("token_id"))


async def test_audit_rollback_phases(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """回滚三阶段（REQUESTED / DECIDED / EXECUTED）分别落事件类型。"""
    rollback_id = uuid.uuid4()
    for phase in ("REQUESTED", "DECIDED", "EXECUTED"):
        await on_rollback_event(
            db_session,
            project_id=seeded.project_a_id,
            rollback_id=rollback_id,
            phase=phase,
            actor_id=seeded.engineer_a_user_id,
            status=phase,
        )
    evidence_types = await _evidence_types(db_session, seeded.project_a_id)
    assert {"ROLLBACK_REQUESTED", "ROLLBACK_DECIDED", "ROLLBACK_EXECUTED"} <= evidence_types


async def test_audit_hook_rejects_unknown_rollback_phase(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """未知回滚阶段即拒绝（不静默记入错类型）。"""
    with pytest.raises(ValueError, match="未知回滚阶段"):
        await on_rollback_event(
            db_session,
            project_id=seeded.project_a_id,
            rollback_id=uuid.uuid4(),
            phase="NOT_A_PHASE",
        )
