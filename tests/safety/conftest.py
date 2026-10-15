"""S9 安全进化用例共享夹具（提案状态机 + SECURITY_APPROVER 角色）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 5.2 章 safety_improvement_proposals
覆盖 FR：FR-51（提案）/ FR-52（受控发布）
对应 AC：AC-01（仅人类安全审批人裁决）
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import ProjectMembership, ProjectRoleGrant
from tests.helpers import SeededWorkspace


@pytest.fixture
async def seeded_security_approver(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> uuid.UUID:
    """给 admin-b 授予项目 A 成员身份 + SECURITY_APPROVER 职责（两条闸门各自独立）。"""
    db_session.add(
        ProjectMembership(
            project_id=seeded.project_a_id, user_id=seeded.admin_b_user_id, role="ADMIN"
        )
    )
    db_session.add(
        ProjectRoleGrant(
            project_id=seeded.project_a_id,
            user_id=seeded.admin_b_user_id,
            role="SECURITY_APPROVER",
            granted_by=seeded.admin_a_user_id,
        )
    )
    await db_session.flush()
    return seeded.admin_b_user_id


@pytest.fixture
async def approved_proposal(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_security_approver: uuid.UUID,
):
    """一条已 APPROVED 的提案（走完整状态机：create → submit → decide）。"""
    from etl_agent.safety import proposals as proposal_service

    row = await proposal_service.create_proposal(
        db_session,
        proposal_service.ProposalInput(
            project_id=seeded.project_a_id,
            type="POLICY",
            title="收紧 P1 预算门禁",
            summary="把预算超限阈值从 2e7 降到 1e7",
            payload={"gate": "budget", "threshold": 10_000_000},
            created_by=seeded.engineer_a_user_id,
        ),
    )
    await proposal_service.submit_proposal(db_session, row.id)
    return await proposal_service.decide_proposal(
        db_session,
        proposal_id=row.id,
        decision="APPROVE",
        decided_by=seeded_security_approver,
        actor_roles=frozenset({"SECURITY_APPROVER"}),
        reason="样本充足，批准",
    )
