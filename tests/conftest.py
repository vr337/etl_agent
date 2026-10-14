"""S2 测试共享 fixtures：应用装配、数据库回滚会话、工作区种子数据。

事务策略：每个测试幂等建表后在同一事务内执行，会话关闭即回滚——不残留数据，
与 tests/infrastructure 的 S1 策略一致；引擎每测试 dispose，避免 asyncpg 连接
跨 pytest-asyncio 事件循环复用（S1 遗留经验）。
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.base import Base
from etl_agent.infrastructure.database import async_session_factory, engine
from etl_agent.infrastructure.models import Project, ProjectMembership, ProjectRoleGrant, User
from etl_agent.infrastructure.redis_client import close_redis_client
from tests.helpers import SeededWorkspace, build_test_app


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """业务库会话：幂等建表 → 交付 → 关闭即回滚。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_session_factory()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


@pytest.fixture
async def app(db_session: AsyncSession) -> AsyncIterator[FastAPI]:
    """dev-header 模式应用（Nonce 走真实 Redis）。"""
    application = await build_test_app(db_session)
    yield application
    await close_redis_client(application.state.redis)


@pytest.fixture
async def gateway_app(db_session: AsyncSession) -> AsyncIterator[FastAPI]:
    """trusted-gateway 模式应用（Nonce 走真实 Redis）。"""
    application = await build_test_app(db_session, auth_mode="trusted-gateway")
    yield application
    await close_redis_client(application.state.redis)


@pytest.fixture
async def seeded(db_session: AsyncSession) -> SeededWorkspace:
    """种子工作区：项目 A（admin-a + engineer-a）与项目 B（admin-b），outsider 无任何成员关系。"""
    project_a = Project(name=f"proj-a-{uuid.uuid4()}")
    project_b = Project(name=f"proj-b-{uuid.uuid4()}")
    db_session.add_all([project_a, project_b])
    await db_session.flush()

    async def make_user(subject: str) -> User:
        user = User(subject=subject, display_name=subject)
        db_session.add(user)
        await db_session.flush()
        return user

    admin_a = await make_user(SeededWorkspace.ADMIN_A)
    engineer_a = await make_user(SeededWorkspace.ENGINEER_A)
    admin_b = await make_user(SeededWorkspace.ADMIN_B)
    outsider = await make_user(SeededWorkspace.OUTSIDER)

    db_session.add_all(
        [
            ProjectMembership(project_id=project_a.id, user_id=admin_a.id, role="ADMIN"),
            ProjectMembership(project_id=project_a.id, user_id=engineer_a.id, role="DATA_ENGINEER"),
            ProjectMembership(project_id=project_b.id, user_id=admin_b.id, role="ADMIN"),
            # engineer-a 在项目 A 持有 DATA_APPROVER Harness 职责（供职责解析测试）
            ProjectRoleGrant(
                project_id=project_a.id,
                user_id=engineer_a.id,
                role="DATA_APPROVER",
                granted_by=admin_a.id,
            ),
        ]
    )
    await db_session.flush()
    return SeededWorkspace(
        project_a_id=project_a.id,
        project_b_id=project_b.id,
        admin_a_user_id=admin_a.id,
        engineer_a_user_id=engineer_a.id,
        admin_b_user_id=admin_b.id,
        outsider_user_id=outsider.id,
    )
