"""M-B 项目与工作区治理 — 共享常量与用户投影。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-B
覆盖 FR：FR-01（Bootstrap）、FR-02（成员管理）、FR-03（Harness 职责授予）
对应 AC：AC-01（职责分离）、AC-12（多租户隔离）
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import User

# 项目成员角色（PRD 2.1 / SPEC 5.2 project_memberships.role）
MEMBER_ROLES = frozenset({"ADMIN", "DATA_ENGINEER", "APPROVER", "VIEWER"})

# Harness 职责角色（PRD 2.1/2.2 / SPEC 5.2 project_role_grants.role）
HARNESS_ROLES = frozenset(
    {"DATA_ENGINEER", "DATA_APPROVER", "SECURITY_APPROVER", "OPERATOR", "AUDITOR"}
)


async def get_or_create_user(session: AsyncSession, subject: str) -> User:
    """按 subject 查用户，不存在则创建（FR-02 成员添加路径）。

    S2 无独立注册入口（禁止事项 15：不实现密码/会话登录），subject 即身份声明，
    users 行是身份在项目内的本地投影；display_name 初始取 subject 本身。

    Args:
        session: 请求级数据库会话。
        subject: 登录主体（users.subject，全局唯一）。

    Returns:
        User: 已存在或新建的用户行（flush 后 id 可用）。
    """
    result = await session.execute(select(User).where(User.subject == subject))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(subject=subject, display_name=subject)
        session.add(user)
        await session.flush()
    return user


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    """按主键查用户（成员移除 / 列表路径）。"""
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()
