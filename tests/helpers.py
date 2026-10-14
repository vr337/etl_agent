"""S2 测试共享辅助：Settings 构造、ASGI 客户端、种子数据模型。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-A/M-B/M-D/M-E/M-F
覆盖 FR：FR-01/02/03/58/65/66（S2/S3）、FR-12/13/14/15（S4 探查与文件资产）、FR-16/17/67（S5 生成与状态隔离）
对应 AC：AC-11（认证边界）、AC-12（多租户隔离）、AC-02（脱敏样本零明文）、AC-03（生成中断与恢复）

测试密钥 / Token 均 ≥32 字节（FR-01/65），仅测试用途，非任何真实凭据。
S5 追加：`build_test_app` 默认注入 `InMemorySaver`——单测不依赖 `etl_checkpoint` 库，
但状态机语义（interrupt / resume / 跨请求恢复）与生产一致；Checkpoint 双库隔离由
`tests/generation/test_checkpoint_isolation.py` 用真实 Postgres Checkpointer 单独验证。
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.config import Settings
from etl_agent.infrastructure.database import get_session
from etl_agent.infrastructure.database import settings as infra_settings
from etl_agent.infrastructure.redis_client import close_redis_client, create_redis_client
from etl_agent.main import create_app

TEST_GATEWAY_SECRET: str = "test-gateway-secret-0123456789abcdef"
TEST_BOOTSTRAP_TOKEN: str = "test-bootstrap-token-0123456789abcdef"

# S4 脱敏验证用样本值（构造值，非真实个人信息）：必须出现在源表、绝不能出现在响应
SAMPLE_EMAILS: tuple[str, ...] = (
    "alice@example.com",
    "bob@example.com",
    "carol@example.com",
)

# 单测内 Fernet key 缓存：保证同一测试里 register（加密）/ app（解密）/ provider（解密）
# 使用同一把密钥——否则跨调用新生成密钥会导致 InvalidToken。每个测试前由 conftest 重置。
_cached_fernet_key: str | None = None


def make_fernet_key() -> str:
    """生成测试用 Fernet key（运行时生成，不硬编码任何密钥材料）。"""
    return Fernet.generate_key().decode()


def _fernet_key_for_test() -> str:
    """返回本测试内稳定的 Fernet key（首次调用生成并缓存）。"""
    global _cached_fernet_key
    if _cached_fernet_key is None:
        _cached_fernet_key = make_fernet_key()
    return _cached_fernet_key


def reset_test_secrets() -> None:
    """每个测试前重置 Fernet key 缓存（conftest 自动调用）。"""
    global _cached_fernet_key
    _cached_fernet_key = None


def make_settings(**overrides: object) -> Settings:
    """构造测试用 Settings（init 参数优先级高于 infra/.env）。

    fernet_key 在单个测试内稳定复用（除非显式覆盖），保证加密/解密同源。
    """
    base: dict[str, object] = {
        "env": "dev",
        "auth_mode": "dev-header",
        "gateway_hmac_secret": TEST_GATEWAY_SECRET,
        "bootstrap_enabled": True,
        "bootstrap_token": TEST_BOOTSTRAP_TOKEN,
        "secret_provider": "fernet-db",
        "fernet_key": overrides.get("fernet_key", _fernet_key_for_test()),
        "fernet_key_version": 1,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def dev_pg_credentials() -> dict[str, object]:
    """开发库 PostgreSQL 连接参数（来自 infra/.env；探针成功路径用）。"""
    return {
        "host": infra_settings.postgres_host,
        "port": infra_settings.postgres_port,
        "database_name": infra_settings.postgres_db,
        "username": infra_settings.postgres_user,
        "password": infra_settings.postgres_password,
    }


async def build_test_app(
    db_session: AsyncSession, checkpointer: Any = None, **overrides: object
) -> FastAPI:
    """装配应用并注入测试会话 / 真实 Redis 客户端（不触发 lifespan）。

    Args:
        db_session: 测试会话（覆盖 `get_session` 依赖）。
        checkpointer: LangGraph Checkpointer；缺省用 `InMemorySaver`（单测不依赖
            `etl_checkpoint` 库，但 interrupt / resume 语义与生产一致）。
        **overrides: Settings 覆盖项。

    Returns:
        FastAPI: 可被 `make_client` 驱动的应用实例。
    """
    application = create_app(make_settings(**overrides))

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    application.dependency_overrides[get_session] = _override_get_session
    application.state.redis = create_redis_client()
    application.state.checkpointer = checkpointer or InMemorySaver()
    return application


@asynccontextmanager
async def built_app(
    db_session: AsyncSession, checkpointer: Any = None, **overrides: object
) -> AsyncIterator[FastAPI]:
    """构造带自定义配置的测试应用，退出时释放 Redis 客户端（S4：文件资产上限覆盖）。"""
    application = await build_test_app(db_session, checkpointer=checkpointer, **overrides)
    try:
        yield application
    finally:
        await close_redis_client(application.state.redis)


def make_client(application: object) -> AsyncClient:
    """ASGI 传输的异步测试客户端（不触发 lifespan，Redis 由 fixture 预置）。"""
    return AsyncClient(transport=ASGITransport(app=application), base_url="http://testserver")  # type: ignore[arg-type]


@dataclass(frozen=True)
class SeededWorkspace:
    """两个项目 + 四类身份：A 管理员 / A 工程师 / B 管理员 / 局外人。"""

    project_a_id: uuid.UUID
    project_b_id: uuid.UUID
    admin_a_user_id: uuid.UUID
    engineer_a_user_id: uuid.UUID
    admin_b_user_id: uuid.UUID
    outsider_user_id: uuid.UUID

    ADMIN_A = "admin-a"
    ENGINEER_A = "engineer-a"
    ADMIN_B = "admin-b"
    OUTSIDER = "outsider"
