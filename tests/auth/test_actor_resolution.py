"""Actor 职责解析测试（T-03.6 / S2 提示词 10）。

覆盖：成员角色 + Harness 职责解析正确、非成员返回空上下文、未知 subject 401。
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.actor_resolver import resolve_actor
from etl_agent.auth.dependencies import require_harness_role
from etl_agent.auth.errors import RoleRequired, UnknownActor
from tests.helpers import SeededWorkspace


async def test_actor_resolver_roles(db_session: AsyncSession, seeded: SeededWorkspace) -> None:
    """成员角色与 Harness 职责均正确解析（FR-65/66）。"""
    actor = await resolve_actor(db_session, SeededWorkspace.ENGINEER_A, seeded.project_a_id)
    assert actor.member_role == "DATA_ENGINEER"
    assert set(actor.harness_roles) == {"DATA_APPROVER"}
    assert actor.subject == SeededWorkspace.ENGINEER_A


async def test_actor_resolver_non_member(db_session: AsyncSession, seeded: SeededWorkspace) -> None:
    """非成员：member_role 为 None、harness_roles 为空（放行判决在依赖层）。"""
    actor = await resolve_actor(db_session, SeededWorkspace.OUTSIDER, seeded.project_a_id)
    assert actor.member_role is None
    assert not actor.harness_roles


async def test_actor_resolver_unknown_subject(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """未知 subject → UnknownActor（401）。"""
    with pytest.raises(UnknownActor):
        await resolve_actor(db_session, "ghost-actor", seeded.project_a_id)


async def test_admin_not_implicitly_security_approver(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """ADMIN 无 SECURITY_APPROVER 授予 → require_harness_role 拒绝（FR-03 / PRD 2.3）。"""
    admin_actor = await resolve_actor(db_session, SeededWorkspace.ADMIN_A, seeded.project_a_id)
    assert admin_actor.member_role == "ADMIN"
    assert not admin_actor.harness_roles
    dependency = require_harness_role("SECURITY_APPROVER")
    with pytest.raises(RoleRequired):
        await dependency(admin_actor)


async def test_granted_harness_role_passes(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """已显式授予 DATA_APPROVER → require_harness_role 放行（FR-03）。"""
    engineer_actor = await resolve_actor(
        db_session, SeededWorkspace.ENGINEER_A, seeded.project_a_id
    )
    dependency = require_harness_role("DATA_APPROVER")
    assert await dependency(engineer_actor) is engineer_actor
