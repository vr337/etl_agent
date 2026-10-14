"""M-B 项目与工作区治理 — 成员管理。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-B
覆盖 FR：FR-02（成员增删、项目角色分配；(project_id, user_id) 唯一）
对应 AC：AC-01（职责分离）、AC-12（多租户隔离）
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.errors import NotFound, StateConflict, UniqueConstraintViolation
from etl_agent.infrastructure.models import ProjectMembership, User
from etl_agent.projects.bootstrap import validate_member_role
from etl_agent.projects.service import get_or_create_user


async def add_member(
    session: AsyncSession, *, project_id: uuid.UUID, subject: str, role: str
) -> ProjectMembership:
    """添加项目成员（FR-02）。

    Args:
        session: 请求级数据库会话。
        project_id: 目标项目。
        subject: 成员登录主体（不存在则投影创建）。
        role: 项目成员角色（4 类）。

    Returns:
        ProjectMembership: 新建成员关系。

    Raises:
        ValidationError:          角色非法。
        UniqueConstraintViolation: (project_id, user_id) 唯一约束冲突（409）。
    """
    validate_member_role(role)
    user = await get_or_create_user(session, subject)
    membership = ProjectMembership(project_id=project_id, user_id=user.id, role=role)
    session.add(membership)
    try:
        await session.flush()
    except IntegrityError as exc:
        # (project_id, user_id) UK：重复添加 → 409（S2 提示词 9.6）
        raise UniqueConstraintViolation("成员已存在") from exc
    return membership


async def remove_member(
    session: AsyncSession, *, project_id: uuid.UUID, user_id: uuid.UUID, actor_user_id: uuid.UUID
) -> None:
    """移除项目成员（FR-02）。

    Raises:
        StateConflict: 尝试移除自己（S2 提示词 9.6：不能移除自己）。
        NotFound:      目标成员不存在（跨项目视同不存在，BIZ_404_NOT_FOUND）。
    """
    if user_id == actor_user_id:
        raise StateConflict("不能移除自己")
    result = await session.execute(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user_id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise NotFound("成员不存在")
    await session.delete(membership)
    await session.flush()


async def update_member_role(
    session: AsyncSession, *, project_id: uuid.UUID, user_id: uuid.UUID, role: str
) -> ProjectMembership:
    """变更成员角色（PATCH，FR-02）。

    Raises:
        ValidationError: 角色非法。
        NotFound:        目标成员不存在。
    """
    validate_member_role(role)
    result = await session.execute(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user_id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise NotFound("成员不存在")
    membership.role = role
    await session.flush()
    return membership


async def list_members(
    session: AsyncSession, *, project_id: uuid.UUID
) -> list[tuple[User, str]]:
    """列出项目成员（FR-02）：返回 (user, role) 列表。"""
    result = await session.execute(
        select(User, ProjectMembership.role)
        .join(ProjectMembership, ProjectMembership.user_id == User.id)
        .where(ProjectMembership.project_id == project_id)
        .order_by(User.subject)
    )
    return [(row[0], row[1]) for row in result.all()]
