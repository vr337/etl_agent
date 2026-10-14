"""M-A 认证鉴权 — Actor → 项目成员 / Harness 职责解析。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-A / 第 8 章「安全 · 授权与职责分离」行
覆盖 FR：FR-65（Actor 身份经项目成员关系与 ProjectRoleGrant 映射为 Harness 职责）、FR-66（多租户隔离）
对应 AC：AC-01（职责分离）、AC-12（多租户隔离）
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.errors import UnknownActor
from etl_agent.infrastructure.models import (
    ProjectMembership,
    ProjectRoleGrant,
    User,
)


@dataclass(frozen=True)
class ActorContext:
    """单请求的 Actor 项目上下文（S2 提示词 9.3）。

    member_role 为 None 表示 actor 不是该项目成员——此时是否放行由调用方
    （require_project_member）判定，解析层不做权限判决（M-A 边界：不做业务授权判决）。
    """

    user_id: uuid.UUID
    subject: str
    project_id: uuid.UUID
    member_role: str | None = None
    harness_roles: frozenset[str] = field(default_factory=frozenset)


async def resolve_user(session: AsyncSession, subject: str) -> User:
    """按 subject 查用户（FR-65：X-Actor-ID 声明 / 网关签名中的 actor 即 subject）。

    Args:
        session: 请求级数据库会话。
        subject: 登录主体（users.subject，全局唯一）。

    Returns:
        User: 命中的用户行。

    Raises:
        UnknownActor: subject 在 users 不存在（401，身份未知）。
    """
    result = await session.execute(select(User).where(User.subject == subject))
    user = result.scalar_one_or_none()
    if user is None:
        raise UnknownActor(f"未知 Actor: {subject}")
    return user


async def resolve_actor(
    session: AsyncSession, subject: str, project_id: uuid.UUID
) -> ActorContext:
    """解析 actor 在目标项目下的成员角色与 Harness 职责（FR-65/66）。

    Args:
        session: 请求级数据库会话。
        subject: 登录主体。
        project_id: 目标项目（隔离边界，FR-66）。

    Returns:
        ActorContext: member_role / harness_roles 为 None / 空集表示非成员或无职责。

    Raises:
        UnknownActor: subject 在 users 不存在（401）。
    """
    user = await resolve_user(session, subject)

    membership_result = await session.execute(
        select(ProjectMembership.role).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user.id,
        )
    )
    member_role: str | None = membership_result.scalar_one_or_none()

    grants_result = await session.execute(
        select(ProjectRoleGrant.role).where(
            ProjectRoleGrant.project_id == project_id,
            ProjectRoleGrant.user_id == user.id,
        )
    )
    harness_roles = frozenset(grants_result.scalars().all())

    return ActorContext(
        user_id=user.id,
        subject=user.subject,
        project_id=project_id,
        member_role=member_role,
        harness_roles=harness_roles,
    )
