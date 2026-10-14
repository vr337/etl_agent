"""S2 测试共享 fixtures：应用装配、数据库回滚会话、工作区种子数据。

事务策略：每个测试幂等建表后在同一事务内执行，会话关闭即回滚——不残留数据，
与 tests/infrastructure 的 S1 策略一致；引擎每测试 dispose，避免 asyncpg 连接
跨 pytest-asyncio 事件循环复用（S1 遗留经验）。

S4 追加：文件资产根目录隔离（`asset_root`）、跨项目连接（`seeded_connection_b`）、
以及**已提交**的探查源表（`profile_source_table`）——asyncpg 探查连接看不到
未提交数据，故脱敏验证必须用独立事务提交后建表，用例结束即 DROP。
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.base import Base
from etl_agent.infrastructure.database import async_session_factory, engine
from etl_agent.infrastructure.models import Project, ProjectMembership, ProjectRoleGrant, User
from etl_agent.infrastructure.redis_client import close_redis_client
from tests.helpers import (
    SAMPLE_EMAILS,
    SeededWorkspace,
    build_test_app,
    dev_pg_credentials,
    make_settings,
    reset_test_secrets,
)


@pytest.fixture(autouse=True)
def _reset_secrets_cache() -> AsyncIterator[None]:
    """每个测试前重置 Fernet key 缓存，保证测试间密钥互不泄露。"""
    reset_test_secrets()
    yield


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


@pytest.fixture
async def seeded_connection(db_session: AsyncSession, seeded: SeededWorkspace):
    """项目 A 下一条指向开发库 PostgreSQL 的连接（Secret 引用制，凭据来自 infra/.env）。

    返回 (connection, settings)：settings 供 Provider/探针使用（与 app fixture 同源配置）。
    """
    from etl_agent.connections.register import register_connection
    from etl_agent.connections.schemas import ConnectionRegisterRequest

    settings = make_settings()
    pg = dev_pg_credentials()
    payload = ConnectionRegisterRequest(
        name=f"pg-dev-{uuid.uuid4()}",
        connector_type="POSTGRESQL",
        host=str(pg["host"]),
        port=int(pg["port"]),  # type: ignore[arg-type]
        database_name=str(pg["database_name"]),
        username=str(pg["username"]),
        password=str(pg["password"]),
    )
    connection = await register_connection(
        db_session, project_id=seeded.project_a_id, payload=payload, settings=settings
    )
    return connection, settings


@pytest.fixture
async def seeded_connection_b(db_session: AsyncSession, seeded: SeededWorkspace):
    """项目 B 下的一条连接（供「项目 A 成员访问项目 B 连接 → 404」用例）。"""
    from etl_agent.connections.register import register_connection
    from etl_agent.connections.schemas import ConnectionRegisterRequest

    settings = make_settings()
    pg = dev_pg_credentials()
    payload = ConnectionRegisterRequest(
        name=f"pg-dev-b-{uuid.uuid4()}",
        connector_type="POSTGRESQL",
        host=str(pg["host"]),
        port=int(pg["port"]),  # type: ignore[arg-type]
        database_name=str(pg["database_name"]),
        username=str(pg["username"]),
        password=str(pg["password"]),
    )
    connection = await register_connection(
        db_session, project_id=seeded.project_b_id, payload=payload, settings=settings
    )
    return connection, settings


@pytest.fixture
def asset_root(tmp_path: Path) -> str:
    """文件资产根目录（隔离到 tmp_path，不触碰 ETL_AGENT_FILE_ASSET_ROOT 默认值）。"""
    return str(tmp_path / "file_assets")


@pytest.fixture
async def profile_source_table(db_session: AsyncSession) -> AsyncIterator[str]:
    """在开发库中**提交**一张含敏感列的样本表（AC-02 脱敏验证用）。

    必须独立事务提交：Profile 探查走 asyncpg 独立连接，看不到未提交 DDL/DML。
    用例结束即 DROP —— 不向开发库留下任何测试资产。

    teardown 先 `rollback()` 再 DROP：测试会话若读过该表会持有 ACCESS SHARE 锁，
    而 DROP 需要 ACCESS EXCLUSIVE —— 不回滚就会在 fixture 与 db_session 之间互等。
    """
    table = f"profile_src_{uuid.uuid4().hex[:8]}"
    async with engine.begin() as conn:
        await conn.execute(
            text(
                f'CREATE TABLE public."{table}" ('
                "id integer PRIMARY KEY, "
                "customer_email text, "
                "amount numeric(10,2), "
                "created_at timestamptz, "
                "note text)"
            )
        )
        for index, email in enumerate(SAMPLE_EMAILS, start=1):
            await conn.execute(
                text(
                    f'INSERT INTO public."{table}" (id, customer_email, amount, created_at, note) '
                    "VALUES (:id, :email, :amount, now(), :note)"
                ),
                {"id": index, "email": email, "amount": f"{index * 10}.50", "note": f"note-{index}"},
            )
    try:
        yield table
    finally:
        await db_session.rollback()
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP TABLE IF EXISTS public."{table}"'))
