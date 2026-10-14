"""数据库引擎、配置与会话工厂（T-02.4）。

配置读取顺序（pydantic-settings）：
1. 环境变量 ETL_AGENT_*；
2. infra/.env（开发期连接件，S1 提示词第四节）；
3. .env（部署期，可选）；
4. 代码内默认值（不含任何凭据）。

双库分离（ADR-03）：业务库 etl_agent（本模块引擎，Alembic 管理）；
检查点库 etl_checkpoint（checkpoint_setup 管理，LangGraph Checkpointer）。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Settings(BaseSettings):
    """应用配置：统一 ETL_AGENT_ 前缀（PRD 9.4 第 3 条）。"""

    model_config = SettingsConfigDict(
        env_prefix="ETL_AGENT_",
        env_file=("infra/.env", ".env"),
        extra="ignore",
    )

    # ---- PostgreSQL 连接件（与 infra/.env / .env.example 对应）----
    postgres_user: str = "etl_agent"
    postgres_password: str = ""
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "etl_agent"
    postgres_db_checkpoint: str = "etl_checkpoint"

    # ---- Redis 连接件 ----
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0

    # ---- 直连覆盖（.env.example 形态，优先级高于连接件拼装）----
    database_url: str | None = None
    checkpoint_database_url: str | None = None

    # ---- 认证（S2 使用）----
    auth_mode: str = "dev-header"
    gateway_hmac_secret: str = ""
    harness_public_keys: str = ""

    def _build_asyncpg_url(self, database: str) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{quote(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{database}"
        )

    @property
    def resolved_database_url(self) -> str:
        """业务库异步连接串（asyncpg）。"""
        return self.database_url or self._build_asyncpg_url(self.postgres_db)

    @property
    def resolved_checkpoint_database_url(self) -> str:
        """检查点库异步连接串（asyncpg）。"""
        return self.checkpoint_database_url or self._build_asyncpg_url(self.postgres_db_checkpoint)

    @property
    def checkpoint_dsn(self) -> str:
        """检查点库 psycopg DSN（langgraph-checkpoint-postgres 基于 psycopg，不识 asyncpg 协议）。"""
        return self.resolved_checkpoint_database_url.replace("+asyncpg", "")


settings = Settings()

engine: AsyncEngine = create_async_engine(settings.resolved_database_url, pool_pre_ping=True)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：请求级会话。"""
    async with async_session_factory() as session:
        yield session
