"""M-A 认证鉴权 — FastAPI 依赖注入（跨项目访问控制）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-A / 第 8 章「安全 · 多租户」行
覆盖 FR：FR-66（多租户项目隔离：访问控制基于项目成员关系）、FR-03（ADMIN 不隐式升级）
对应 AC：AC-12（非成员访问项目资源 → 403）

S6 追加 `require_actor_for_project`：API-06/07/09/10 等路径中不含 `project_id`，
需先由资源回溯所属项目再做成员校验；抽到本模块避免各路由重复实现（S5 起已有三处同类逻辑）。
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.actor_resolver import ActorContext, resolve_actor
from etl_agent.auth.errors import NotProjectMember, RoleRequired, UnknownActor
from etl_agent.infrastructure.database import get_session

ProjectMemberDep = Callable[..., Awaitable[ActorContext]]


async def require_project_member(
    project_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ActorContext:
    """要求 actor 是目标项目成员，并注入 ActorContext（FR-66 / S2 提示词 9.4）。

    不泄露项目是否存在：项目不存在与无权限统一返回 403（AC-12），
    攻击者无法通过响应差异枚举项目 ID。

    Args:
        project_id: 路径参数（隔离边界）。
        request: 当前请求（读取中间件注入的 actor_subject）。
        session: 请求级数据库会话。

    Returns:
        ActorContext: 成员角色 + Harness 职责。

    Raises:
        UnknownActor:    中间件未注入身份或 subject 未知（401）。
        NotProjectMember: 非成员或项目不存在（403，统一码不泄露存在性）。
    """
    subject = getattr(request.state, "actor_subject", None)
    if not subject:
        raise UnknownActor("请求未携带身份声明")
    actor = await resolve_actor(session, subject, project_id)
    if actor.member_role is None:
        raise NotProjectMember()
    request.state.actor = actor
    return actor


async def require_actor_for_project(
    request: Request, session: AsyncSession, project_id: uuid.UUID
) -> ActorContext:
    """由资源回溯项目后的成员校验（路径无 `project_id` 的端点使用）。

    Args:
        request: 当前请求（读取中间件注入的 actor_subject）。
        session: 请求级数据库会话。
        project_id: 资源所属项目（调用方已从资源反查）。

    Returns:
        ActorContext: 成员角色 + Harness 职责。

    Raises:
        UnknownActor:     未携带身份声明（401）。
        NotProjectMember: 非成员（403）。
    """
    subject = getattr(request.state, "actor_subject", None)
    if not subject:
        raise UnknownActor("请求未携带身份声明")
    actor = await resolve_actor(session, subject, project_id)
    if actor.member_role is None:
        raise NotProjectMember()
    return actor


async def require_project_admin(actor: ActorContext = Depends(require_project_member)) -> ActorContext:
    """要求 actor 具备项目管理员（ADMIN）成员角色（S2 提示词 9.6/9.7：写操作需 ADMIN）。

    Raises:
        RoleRequired: 成员角色非 ADMIN（403）。
    """
    if actor.member_role != "ADMIN":
        raise RoleRequired("需要项目管理员（ADMIN）角色")
    return actor


def require_harness_role(*required_roles: str) -> ProjectMemberDep:
    """构造「要求指定 Harness 职责」的依赖（FR-03 / PRD 2.3 职责分离红线）。

    ADMIN 不隐式升级：harness_roles 仅来自 project_role_grants 显式授予，
    项目管理员身份不带来任何安全审批能力（PRD 2.1：不因此获得审批特权）。

    Args:
        *required_roles: 需要的 Harness 职责（DATA_APPROVER / SECURITY_APPROVER / ...）。

    Returns:
        依赖函数：校验通过返回 ActorContext，否则抛 RoleRequired（403）。
    """

    async def _dependency(actor: ActorContext = Depends(require_project_member)) -> ActorContext:
        missing = [role for role in required_roles if role not in actor.harness_roles]
        if missing:
            raise RoleRequired(f"缺少 Harness 职责: {', '.join(missing)}")
        return actor

    return _dependency
