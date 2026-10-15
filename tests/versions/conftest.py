"""S6 版本域测试基建（T-11）：多人成员关系与审批职责。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-H / 第 5.2 章 approval_requests / 第 8 章「安全 · 授权与职责分离」
覆盖 FR：FR-28（设计审批：ApprovalRequest 绑定 artifact_sha256、禁自批）、FR-66（项目隔离）
对应 AC：AC-01（职责分离）

设计审批需要「申请人 ≠ 审批人」且审批人持 DATA_APPROVER / SECURITY_APPROVER；
`seeded` fixture 默认只给 admin-a（A 项目）与 admin-b（B 项目），故此处补齐
A 项目的第二个独立自然人（admin-b）+ 其 SECURITY_APPROVER 职责。
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import ProjectMembership, ProjectRoleGrant
from tests.helpers import SeededWorkspace


@pytest.fixture
async def design_approvers(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> AsyncIterator[dict[str, uuid.UUID]]:
    """设计审批的二人组（FR-28）：申请人 engineer-a + 审批人 admin-b。

    admin-b 默认只属项目 B，故补一条 A 项目成员关系并授予 SECURITY_APPROVER
    （设计审批可用的三种职责之一，见 `versions/approval.py`）。
    """
    db_session.add(
        ProjectMembership(
            project_id=seeded.project_a_id,
            user_id=seeded.admin_b_user_id,
            role="VIEWER",
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
    return {
        "requester": seeded.engineer_a_user_id,
        "approver": seeded.admin_b_user_id,
    }
