"""M-B 项目与工作区治理 — Harness 职责授予 / 撤销。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-B
覆盖 FR：FR-03（ProjectRoleGrant 显式授予 5 类 Harness 职责；不把管理员隐式升级；记录授予人）
对应 AC：AC-01（职责分离：申请人禁止自批的前置——职责仅来自显式授予）
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.errors import NotFound, ValidationError
from etl_agent.infrastructure.models import ProjectRoleGrant, User
from etl_agent.projects.service import HARNESS_ROLES, get_or_create_user


def validate_harness_role(role: str) -> str:
    """校验 Harness 职责取值（5 类，PRD 2.2）。

    Raises:
        ValidationError: 角色不在 5 类之内。
    """
    if role not in HARNESS_ROLES:
        raise ValidationError(f"非法 Harness 职责: {role!r}，可选 {sorted(HARNESS_ROLES)}")
    return role


async def grant_role(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    subject: str,
    role: str,
    granted_by: uuid.UUID,
) -> ProjectRoleGrant:
    """授予 Harness 职责（FR-03）。

    Args:
        session: 请求级数据库会话。
        project_id: 目标项目。
        subject: 被授予人登录主体（不存在则投影创建——Harness 职责可授予非成员，PRD 2.1）。
        role: Harness 职责（5 类）。
        granted_by: 授予人 user_id（= 当前 actor，服务端注入，FR-03「记录授予人」）。

    Returns:
        ProjectRoleGrant: 新建授予记录。

    Raises:
        ValidationError: 角色非法。
    """
    validate_harness_role(role)
    user = await get_or_create_user(session, subject)
    grant = ProjectRoleGrant(
        project_id=project_id, user_id=user.id, role=role, granted_by=granted_by
    )
    session.add(grant)
    await session.flush()
    return grant


async def revoke_role(
    session: AsyncSession, *, project_id: uuid.UUID, grant_id: uuid.UUID
) -> None:
    """撤销 Harness 职责（FR-03）。

    Raises:
        NotFound: 授予记录不存在（跨项目视同不存在）。
    """
    result = await session.execute(
        select(ProjectRoleGrant).where(
            ProjectRoleGrant.id == grant_id,
            ProjectRoleGrant.project_id == project_id,
        )
    )
    grant = result.scalar_one_or_none()
    if grant is None:
        raise NotFound("职责授予记录不存在")
    await session.delete(grant)
    await session.flush()


async def list_role_grants(
    session: AsyncSession, *, project_id: uuid.UUID
) -> list[tuple[ProjectRoleGrant, User]]:
    """列出项目 Harness 职责授予（FR-03）：返回 (grant, user) 列表。"""
    result = await session.execute(
        select(ProjectRoleGrant, User)
        .join(User, User.id == ProjectRoleGrant.user_id)
        .where(ProjectRoleGrant.project_id == project_id)
        .order_by(ProjectRoleGrant.created_at)
    )
    return [(row[0], row[1]) for row in result.all()]
