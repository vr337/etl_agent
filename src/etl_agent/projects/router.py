"""M-B 项目与工作区治理 — API 路由（API-01 + 成员 / 职责端点）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-01 / 第 3.2 章 M-B
覆盖 FR：FR-01（bootstrap）、FR-02（成员管理）、FR-03（职责授予）
对应 AC：AC-01（职责分离）、AC-12（多租户隔离）
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.actor_resolver import ActorContext
from etl_agent.auth.dependencies import require_project_admin, require_project_member
from etl_agent.auth.errors import NotFound
from etl_agent.infrastructure.database import get_session
from etl_agent.projects import bootstrap, memberships, role_grants
from etl_agent.projects.schemas import (
    BootstrapRequest,
    BootstrapResponse,
    MemberAddRequest,
    MemberResponse,
    MemberRoleUpdateRequest,
    RoleGrantRequest,
    RoleGrantResponse,
)
from etl_agent.projects.service import get_or_create_user, get_user_by_id

router = APIRouter(prefix="/api/v1", tags=["projects"])


@router.post("/bootstrap", status_code=201, response_model=BootstrapResponse)
async def bootstrap_workspace(
    body: BootstrapRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> BootstrapResponse:
    """工作区初始化（API-01，FR-01）。

    Header: X-Bootstrap-Token。幂等：已有项目 → 409。

    提交语义：`bootstrap_workspace` 只 flush，**必须在本路由 commit**——
    `get_session` 的 `async with` 在退出时回滚未提交事务，漏 commit 会让
    初始化「返回 201 但数据全部丢失」，后续所有请求因用户不存在而 401。
    """
    settings = request.app.state.settings
    project_id, admin_user_id = await bootstrap.bootstrap_workspace(
        session,
        project_name=body.project_name,
        admin_subject=body.admin_subject,
        bootstrap_enabled=settings.bootstrap_enabled,
        expected_token=settings.bootstrap_token,
        provided_token=request.headers.get("X-Bootstrap-Token", ""),
    )
    await session.commit()
    return BootstrapResponse(project_id=project_id, admin_user_id=admin_user_id)


@router.get("/projects/{project_id}/members", response_model=list[MemberResponse])
async def list_members(
    project_id: uuid.UUID,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> list[MemberResponse]:
    """列出项目成员（FR-02）。"""
    rows = await memberships.list_members(session, project_id=project_id)
    return [
        MemberResponse(user_id=user.id, subject=user.subject, display_name=user.display_name, role=role)
        for user, role in rows
    ]


@router.post("/projects/{project_id}/members", status_code=201, response_model=MemberResponse)
async def add_member(
    project_id: uuid.UUID,
    body: MemberAddRequest,
    actor: ActorContext = Depends(require_project_admin),
    session: AsyncSession = Depends(get_session),
) -> MemberResponse:
    """添加项目成员（FR-02，写操作需 ADMIN）。重复添加 → 409。"""
    membership = await memberships.add_member(
        session, project_id=project_id, subject=body.subject, role=body.role
    )
    user = await get_or_create_user(session, body.subject)
    await session.commit()
    return MemberResponse(
        user_id=membership.user_id, subject=user.subject, display_name=user.display_name, role=membership.role
    )


@router.delete("/projects/{project_id}/members/{user_id}", status_code=204)
async def remove_member(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    actor: ActorContext = Depends(require_project_admin),
    session: AsyncSession = Depends(get_session),
) -> None:
    """移除项目成员（FR-02，写操作需 ADMIN；不能移除自己）。"""
    await memberships.remove_member(
        session, project_id=project_id, user_id=user_id, actor_user_id=actor.user_id
    )
    await session.commit()


@router.patch("/projects/{project_id}/members/{user_id}", response_model=MemberResponse)
async def update_member_role(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    body: MemberRoleUpdateRequest,
    actor: ActorContext = Depends(require_project_admin),
    session: AsyncSession = Depends(get_session),
) -> MemberResponse:
    """变更成员角色（FR-02，写操作需 ADMIN）。"""
    membership = await memberships.update_member_role(
        session, project_id=project_id, user_id=user_id, role=body.role
    )
    user = await get_user_by_id(session, user_id)
    if user is None:  # 成员存在则用户必然存在（FK 保证）；防御性分支
        raise NotFound("用户不存在")
    await session.commit()
    return MemberResponse(
        user_id=user.id, subject=user.subject, display_name=user.display_name, role=membership.role
    )


@router.get("/projects/{project_id}/role-grants", response_model=list[RoleGrantResponse])
async def list_role_grants(
    project_id: uuid.UUID,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> list[RoleGrantResponse]:
    """列出项目 Harness 职责授予（FR-03）。"""
    rows = await role_grants.list_role_grants(session, project_id=project_id)
    return [
        RoleGrantResponse(
            id=grant.id,
            user_id=grant.user_id,
            subject=user.subject,
            role=grant.role,
            granted_by=grant.granted_by,
            created_at=grant.created_at,
        )
        for grant, user in rows
    ]


@router.post("/projects/{project_id}/role-grants", status_code=201, response_model=RoleGrantResponse)
async def grant_role(
    project_id: uuid.UUID,
    body: RoleGrantRequest,
    actor: ActorContext = Depends(require_project_admin),
    session: AsyncSession = Depends(get_session),
) -> RoleGrantResponse:
    """授予 Harness 职责（FR-03，写操作需 ADMIN；granted_by = 当前 actor）。"""
    grant = await role_grants.grant_role(
        session,
        project_id=project_id,
        subject=body.subject,
        role=body.role,
        granted_by=actor.user_id,
    )
    user = await get_user_by_id(session, grant.user_id)
    if user is None:
        raise NotFound("用户不存在")
    await session.commit()
    return RoleGrantResponse(
        id=grant.id,
        user_id=grant.user_id,
        subject=user.subject,
        role=grant.role,
        granted_by=grant.granted_by,
        created_at=grant.created_at,
    )


@router.delete("/projects/{project_id}/role-grants/{grant_id}", status_code=204)
async def revoke_role(
    project_id: uuid.UUID,
    grant_id: uuid.UUID,
    actor: ActorContext = Depends(require_project_admin),
    session: AsyncSession = Depends(get_session),
) -> None:
    """撤销 Harness 职责（FR-03，写操作需 ADMIN）。"""
    await role_grants.revoke_role(session, project_id=project_id, grant_id=grant_id)
    await session.commit()
