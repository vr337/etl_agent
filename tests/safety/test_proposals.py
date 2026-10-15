"""S9 安全进化 — 提案状态机用例（FR-51 / AC-01）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 5.2 章 safety_improvement_proposals
覆盖 FR：FR-51（类型限定；候选非可执行；状态机不可跳跃；仅人类安全审批人裁决）
对应 AC：AC-01（职责分离：安全维度独立审批）
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.errors import RoleRequired
from etl_agent.safety.errors import SafetyProposalNotFound, SafetyStateConflict
from etl_agent.safety.proposals import (
    ALLOWED_TRANSITIONS,
    PROPOSAL_TYPES,
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_IN_REVIEW,
    STATUS_REJECTED,
    STATUS_RELEASED,
    TERMINAL_STATUSES,
    ProposalInput,
    create_proposal,
    decide_proposal,
    ensure_transition_allowed,
    get_proposal,
    list_proposals,
    looks_executable,
    mark_released,
    submit_proposal,
)
from tests.helpers import SeededWorkspace


async def _draft(
    session: AsyncSession, seeded: SeededWorkspace, **overrides: object
) -> object:
    kwargs: dict[str, object] = {
        "project_id": seeded.project_a_id,
        "type": "POLICY",
        "title": "收紧预算门禁",
        "summary": "把上限降到 1e7",
        "payload": {"gate": "budget", "threshold": 10_000_000},
        "created_by": seeded.engineer_a_user_id,
    }
    kwargs.update(overrides)
    return await create_proposal(session, ProposalInput(**kwargs))  # type: ignore[arg-type]


def test_state_machine_shape() -> None:
    """**FR-51**：状态机不可跳跃（转移表即契约）。"""
    assert ALLOWED_TRANSITIONS[STATUS_DRAFT] == frozenset({STATUS_IN_REVIEW})
    assert ALLOWED_TRANSITIONS[STATUS_IN_REVIEW] == frozenset(
        {STATUS_APPROVED, STATUS_REJECTED}
    )
    assert ALLOWED_TRANSITIONS[STATUS_APPROVED] == frozenset({STATUS_RELEASED})
    assert ALLOWED_TRANSITIONS[STATUS_REJECTED] == frozenset()
    assert ALLOWED_TRANSITIONS[STATUS_RELEASED] == frozenset()
    assert TERMINAL_STATUSES == frozenset({STATUS_REJECTED, STATUS_RELEASED})


def test_proposal_types_limited() -> None:
    """**FR-51**：提案类型限定为 POLICY / PROMPT / BENCHMARK。"""
    assert PROPOSAL_TYPES == frozenset({"POLICY", "PROMPT", "BENCHMARK"})


def test_ensure_transition_rejects_jump() -> None:
    """DRAFT → APPROVED 直通被拒（必须先 IN_REVIEW）。"""
    with pytest.raises(SafetyStateConflict, match="不可跳跃"):
        ensure_transition_allowed(STATUS_DRAFT, STATUS_APPROVED)
    with pytest.raises(SafetyStateConflict):
        ensure_transition_allowed(STATUS_IN_REVIEW, STATUS_RELEASED)


def test_ensure_transition_allows_legal() -> None:
    """合法转移不抛。"""
    ensure_transition_allowed(STATUS_DRAFT, STATUS_IN_REVIEW)
    ensure_transition_allowed(STATUS_IN_REVIEW, STATUS_APPROVED)
    ensure_transition_allowed(STATUS_APPROVED, STATUS_RELEASED)


def test_looks_executable_detects_code() -> None:
    """**FR-51**：候选含可执行代码特征即被识别。"""
    assert looks_executable({"script": "def hack(): import os"}) is True
    assert looks_executable({"snippet": "subprocess.run(['rm','-rf','/'])"}) is True
    assert looks_executable({"gate": "budget", "threshold": 1}) is False
    assert looks_executable(None) is False


@pytest.mark.asyncio
async def test_create_proposal_draft(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """创建提案 → DRAFT。"""
    row = await _draft(db_session, seeded)
    assert row.status == STATUS_DRAFT
    assert row.type == "POLICY"
    assert row.decided_by is None


@pytest.mark.asyncio
async def test_create_rejects_unknown_type(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """**FR-51**：类型非法 → 拒绝创建。"""
    with pytest.raises(SafetyStateConflict, match="类型非法"):
        await _draft(db_session, seeded, type="SHELL")


@pytest.mark.asyncio
async def test_create_rejects_executable_payload(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """**FR-51**：候选含可执行代码 → 拒绝创建。"""
    with pytest.raises(SafetyStateConflict, match="可执行代码"):
        await _draft(db_session, seeded, payload={"script": "import subprocess"})


@pytest.mark.asyncio
async def test_full_happy_path(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """DRAFT → IN_REVIEW → APPROVED → RELEASED（合法路径走通）。"""
    row = await _draft(db_session, seeded)
    row = await submit_proposal(db_session, row.id)
    assert row.status == STATUS_IN_REVIEW
    row = await decide_proposal(
        db_session,
        proposal_id=row.id,
        decision="APPROVE",
        decided_by=seeded.admin_b_user_id,
        actor_roles=frozenset({"SECURITY_APPROVER"}),
        reason="批准",
    )
    assert row.status == STATUS_APPROVED
    assert row.decided_by == seeded.admin_b_user_id
    assert row.decision == "APPROVE"
    assert row.decided_at is not None
    row = await mark_released(db_session, row.id)
    assert row.status == STATUS_RELEASED


@pytest.mark.asyncio
async def test_submit_twice_conflicts(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """重复提交 → 409（IN_REVIEW → IN_REVIEW 非法）。"""
    row = await _draft(db_session, seeded)
    await submit_proposal(db_session, row.id)
    with pytest.raises(SafetyStateConflict):
        await submit_proposal(db_session, row.id)


@pytest.mark.asyncio
async def test_decide_without_submit_conflicts(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """**FR-51**：跳过 IN_REVIEW 直接裁决 → 409（不可跳跃）。"""
    row = await _draft(db_session, seeded)
    with pytest.raises(SafetyStateConflict, match="不可跳跃"):
        await decide_proposal(
            db_session,
            proposal_id=row.id,
            decision="APPROVE",
            decided_by=seeded.admin_b_user_id,
            actor_roles=frozenset({"SECURITY_APPROVER"}),
        )


@pytest.mark.asyncio
async def test_decide_requires_security_approver_role(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """**AC-01**：无 SECURITY_APPROVER 职责 → 403（ADMIN 不隐式升级）。"""
    row = await _draft(db_session, seeded)
    await submit_proposal(db_session, row.id)
    with pytest.raises(RoleRequired):
        await decide_proposal(
            db_session,
            proposal_id=row.id,
            decision="APPROVE",
            decided_by=seeded.admin_a_user_id,
            actor_roles=frozenset({"ADMIN", "DATA_APPROVER"}),
        )


@pytest.mark.asyncio
async def test_decide_invalid_decision_value(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """裁决值非法 → 409。"""
    row = await _draft(db_session, seeded)
    await submit_proposal(db_session, row.id)
    with pytest.raises(SafetyStateConflict, match="裁决值非法"):
        await decide_proposal(
            db_session,
            proposal_id=row.id,
            decision="MAYBE",
            decided_by=seeded.admin_b_user_id,
            actor_roles=frozenset({"SECURITY_APPROVER"}),
        )


@pytest.mark.asyncio
async def test_reject_then_terminal(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """REJECTED 为终态：不可再提交 / 再裁决。"""
    row = await _draft(db_session, seeded)
    await submit_proposal(db_session, row.id)
    row = await decide_proposal(
        db_session,
        proposal_id=row.id,
        decision="REJECT",
        decided_by=seeded.admin_b_user_id,
        actor_roles=frozenset({"SECURITY_APPROVER"}),
        reason="样本不足",
    )
    assert row.status == STATUS_REJECTED
    with pytest.raises(SafetyStateConflict):
        await mark_released(db_session, row.id)


@pytest.mark.asyncio
async def test_get_missing_proposal_404(db_session: AsyncSession) -> None:
    """不存在的提案 → 404。"""
    with pytest.raises(SafetyProposalNotFound):
        await get_proposal(db_session, uuid.uuid4())


@pytest.mark.asyncio
async def test_list_proposals_scoped_to_project(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """提案列表按项目隔离（AC-12）。"""
    await _draft(db_session, seeded)
    assert len(await list_proposals(db_session, seeded.project_a_id)) == 1
    assert await list_proposals(db_session, seeded.project_b_id) == []


@pytest.mark.asyncio
async def test_proposal_view_omits_payload_via_router() -> None:
    """契约：`ProposalView` 不含 payload 字段（未审内容不入响应）。"""
    from etl_agent.safety.schemas import ProposalView

    assert "payload" not in ProposalView.model_fields
