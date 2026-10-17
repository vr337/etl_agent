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


def attach_harness_services(application: FastAPI) -> FastAPI:
    """给测试应用注入 Harness 服务（S6：PDP / Prepare / Commit，与 lifespan 装配同源）。

    策略包与密钥取自仓库交付物 + `infra/.env` 的人类配置，测试不新增密钥材料（AC-02）。
    """
    from etl_agent.harness.capability.issuer import CapabilityIssuer
    from etl_agent.harness.commit.service import CommitService
    from etl_agent.harness.pdp.engine import PdpEngine
    from etl_agent.harness.policy.package import load_package
    from etl_agent.harness.prepare.service import PrepareService

    settings = make_settings()
    package = load_package(
        settings.policy_package_path,
        settings.policy_package_signature_path,
        settings.harness_public_key_list,
    )
    application.state.pdp_package = package
    application.state.prepare_service = PrepareService(
        PdpEngine(package),
        requested_ttl_seconds=settings.harness_preparation_ttl_seconds,
    )
    application.state.commit_service = CommitService(
        CapabilityIssuer(settings.harness_signing_key_hex),
        ttl_seconds=settings.harness_capability_ttl_seconds,
    )
    return application


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


# ============================================================
# S7：执行 / 调度用例的共享测试替身
# ============================================================

# 含全部 8 个端点占位符的候选 HOCON（物化用例的输入形态，与 S6 登记契约一致）
S7_TEST_HOCON = (
    "env {\n"
    '  job.mode = "BATCH"\n'
    "}\n"
    "source {\n"
    "  Jdbc {\n"
    '    host = "${SRC_HOST}"\n'
    '    port = "${SRC_PORT}"\n'
    '    database = "${SRC_DB}"\n'
    '    username = "${SRC_USER}"\n'
    '    password = "${SRC_PASSWORD}"\n'
    "  }\n"
    "}\n"
    "sink {\n"
    "  Jdbc {\n"
    '    host = "${SINK_HOST}"\n'
    '    port = "${SINK_PORT}"\n'
    '    database = "${SINK_DB}"\n'
    '    username = "${SINK_USER}"\n'
    '    password = "${SINK_PASSWORD}"\n'
    "  }\n"
    "}\n"
)


class FakeSeaTunnelClient:
    """SeaTunnel 客户端测试替身（记录调用，按脚本回传状态）。

    不发起任何网络 / 子进程调用：REST 与 CLI 通道的真实性由
    `tests/worker/test_seatunnel_client.py` 用 httpx MockTransport 与
    伪 CLI 脚本单独验证。
    """

    def __init__(
        self,
        *,
        statuses: list[str] | None = None,
        check_ok: bool = True,
        job_id: str = "seatunnel-job-1",
    ) -> None:
        self.submitted: list[str] = []
        self.job_names: list[str] = []
        self.checked: list[str] = []
        self.stopped: list[str] = []
        self._statuses = list(statuses or ["FINISHED"])
        self._check_ok = check_ok
        self._job_id = job_id
        # 默认样本量 >= SPEC 5.2 冻结预算的「最低质量行数 100」——
        # 样本不足 100 行按 INCOMPLETE 处理（判不了通过），故 happy path 必须用足量样本。
        # 拒绝行数取 2（拒绝率 0.2%）：与真实 seed 同量级，且**稳稳落在
        # `max_reject_rate: 0.05` 之内**。历史上这里是 100/1000 = 10%，
        # 靠契约 fail-open（无阈值）才判成 PASSED —— T-QUALITY-01 已修，
        # 现在它会被真实判为 FAILED，故一并改成干净样本。
        self.metrics: dict[str, object] = {
            "source_rows": 1000,
            "sink_rows": 998,
            "source_bytes": 102_400,
            "sink_bytes": 90_000,
            "tables": [{"table": "orders_wide", "rows": 998, "bytes": 90_000}],
            "quality": {"total_rows": 1000, "passed_rows": 998, "rejected_rows": 2,
                        "error_codes": {"NULL_ID": 2}},
        }

    async def submit_job(self, hocon: str, *, job_name: str = "") -> str:
        self.submitted.append(hocon)
        self.job_names.append(job_name)
        return self._job_id

    async def job_status(self, job_id: str) -> object:
        from etl_agent.worker.seatunnel_client import EngineJobStatus

        status = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        return EngineJobStatus(job_id=job_id, status=status, metrics=dict(self.metrics))

    async def stop_job(self, job_id: str) -> None:
        self.stopped.append(job_id)

    async def check(self, hocon: str) -> dict[str, object]:
        self.checked.append(hocon)
        return {"ok": self._check_ok, "summary": "engine check passed" if self._check_ok else "boom"}


class RecordingDispatcher:
    """API-11 即时派发的测试替身（记录被调用次数，不连 Broker）。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self._fail = fail

    def __call__(self) -> None:
        self.calls += 1
        if self._fail:
            raise RuntimeError("broker unavailable")


@dataclass(frozen=True)
class SeededExecution:
    """已 Commit 的受管执行事实（AC-06 用例的起点，全部为治理事实与引用）。"""

    project_id: uuid.UUID
    version_id: uuid.UUID
    preparation_id: uuid.UUID
    run_id: uuid.UUID
    outbox_id: uuid.UUID
    token_id: uuid.UUID
    token: str
    connection_id: uuid.UUID
    plan: dict[str, object]
    hocon: str
    handoff: object


