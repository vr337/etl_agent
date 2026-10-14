"""Alembic 迁移环境（异步引擎，T-02.3）。

- target_metadata 来自 infrastructure.base.Base（models 全部注册）；
- 连接串运行期从 Settings 注入（不写入 alembic.ini，保证凭据零明文）。
"""
from __future__ import annotations

from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config

from etl_agent.infrastructure.base import Base
from etl_agent.infrastructure.database import settings

# 导入即注册全部 ORM 表（autogenerate 依赖）。
from etl_agent.infrastructure import models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    return settings.resolved_database_url


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL 脚本，不连接数据库。"""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Any) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(section, prefix="sqlalchemy.")
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    import asyncio

    asyncio.run(_run_async_migrations())
