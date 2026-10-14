"""M-B 项目与工作区治理 — 工作区初始化（API-01）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-01 / 第 3.2 章 M-B
覆盖 FR：FR-01（受 bootstrap_enabled + Bootstrap Token ≥32 字节保护的首次初始化）
对应 AC：AC-01（创建首个项目与管理员身份）
"""
from __future__ import annotations

import hmac
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.errors import BootstrapDisabled, BootstrapTokenInvalid, StateConflict
from etl_agent.infrastructure.models import Project, ProjectMembership
from etl_agent.projects.service import MEMBER_ROLES, get_or_create_user

# 首个项目的管理员固定为 ADMIN 成员角色（PRD 2.1：项目与成员治理）
BOOTSTRAP_ADMIN_ROLE = "ADMIN"


async def workspace_initialized(session: AsyncSession) -> bool:
    """是否已有项目（幂等判定：已有 → 409，FR-01）。"""
    result = await session.execute(select(func.count()).select_from(Project))
    return (result.scalar() or 0) > 0


async def bootstrap_workspace(
    session: AsyncSession,
    *,
    project_name: str,
    admin_subject: str,
    bootstrap_enabled: bool,
    expected_token: str,
    provided_token: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    """初始化工作区：创建首个项目 + 管理员用户 + ADMIN 成员关系（FR-01 / API-01）。

    Args:
        session: 请求级数据库会话。
        project_name: 项目名称。
        admin_subject: 管理员登录主体（users.subject）。
        bootstrap_enabled: ETL_AGENT_BOOTSTRAP_ENABLED。
        expected_token: ETL_AGENT_BOOTSTRAP_TOKEN（≥32 字节，启动期已校验）。
        provided_token: 请求头 X-Bootstrap-Token。

    Returns:
        (project_id, admin_user_id)。

    Raises:
        BootstrapDisabled:    未启用初始化（403）。
        BootstrapTokenInvalid: Token 不匹配（403）。
        StateConflict:        工作区已初始化（409，幂等保护）。
    """
    if not bootstrap_enabled:
        raise BootstrapDisabled()
    # compare_digest 常量时间比对，防时序侧信道（SPEC 8「安全 · 认证」行）
    if not hmac.compare_digest(expected_token, provided_token):
        raise BootstrapTokenInvalid()
    if await workspace_initialized(session):
        # 幂等：二次初始化不改动既有工作区，直接冲突（S2 提示词 9.5）
        raise StateConflict("工作区已初始化")

    project = Project(name=project_name)
    session.add(project)
    await session.flush()

    admin = await get_or_create_user(session, admin_subject)
    membership = ProjectMembership(
        project_id=project.id, user_id=admin.id, role=BOOTSTRAP_ADMIN_ROLE
    )
    session.add(membership)
    await session.flush()
    return project.id, admin.id


def validate_member_role(role: str) -> str:
    """校验项目成员角色取值（4 类，PRD 2.1）。

    Raises:
        ValidationError: 角色不在 4 类之内。
    """
    from etl_agent.auth.errors import ValidationError

    if role not in MEMBER_ROLES:
        raise ValidationError(f"非法成员角色: {role!r}，可选 {sorted(MEMBER_ROLES)}")
    return role
