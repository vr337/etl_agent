"""S2 测试共享 fixtures：应用装配、数据库回滚会话、工作区种子数据。

事务策略：每个测试幂等建表后在同一事务内执行，会话关闭即回滚——不残留数据，
与 tests/infrastructure 的 S1 策略一致；引擎每测试 dispose，避免 asyncpg 连接
跨 pytest-asyncio 事件循环复用（S1 遗留经验）。

S4 追加：文件资产根目录隔离（`asset_root`）、跨项目连接（`seeded_connection_b`）、
以及**已提交**的探查源表（`profile_source_table`）——asyncpg 探查连接看不到
未提交数据，故脱敏验证必须用独立事务提交后建表，用例结束即 DROP。

S5 追加：`seeded_profiles`（来源 / 目标画像）与 `seeded_pipeline_version`（生成目标版本），
供生成状态机与门禁用例复用；事件循环策略在导入期切到 Selector（psycopg 异步要求）。
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.base import Base
from etl_agent.infrastructure.database import engine
from etl_agent.infrastructure.models import (
    DataProfile,
    Pipeline,
    PipelineVersion,
    Project,
    ProjectMembership,
    ProjectRoleGrant,
    User,
)
from etl_agent.infrastructure.redis_client import close_redis_client
from tests.helpers import (
    S7_TEST_HOCON,
    SAMPLE_EMAILS,
    SeededExecution,
    SeededWorkspace,
    attach_harness_services,
    build_test_app,
    dev_pg_credentials,
    make_settings,
    reset_test_secrets,
)

# langgraph-checkpoint-postgres 基于 psycopg（v3）：异步模式在 Windows 上不兼容默认的
# ProactorEventLoop，必须显式切 Selector（uvicorn 生产侧已默认切换，测试侧需自行设置）。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(autouse=True)
def _reset_secrets_cache() -> AsyncIterator[None]:
    """每个测试前重置 Fernet key 缓存，保证测试间密钥互不泄露。"""
    reset_test_secrets()
    yield


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """业务库会话：幂等建表 → 交付 → 关闭即回滚。

    S5 起用「外层事务 + savepoint」模式（`join_transaction_mode="create_savepoint"`）：
    生成服务内部会 `commit()`（运行状态 / 遥测必须落库），若直接提交会连测试夹具的
    种子数据一起提交、污染后续用例；savepoint 模式下应用侧 commit 只释放 savepoint，
    外层事务仍由本夹具统一回滚——用例之间依旧零残留。
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    connection = await engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


@pytest.fixture
async def app(db_session: AsyncSession) -> AsyncIterator[FastAPI]:
    """dev-header 模式应用（Nonce 走真实 Redis）。

    S6 起默认注入 Harness 服务（验签后的 PDP / Prepare / Commit）——策略包与密钥取自
    `configs/policy/harness-v2-20260808.*` 与 `infra/.env` 的人类配置，测试不新增密钥材料。
    """
    application = await build_test_app(db_session)
    attach_harness_services(application)
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


# ============================================================
# S5：生成状态机 / 门禁用例的共享夹具
# ============================================================

# 画像列（来源含时间列，供 GATE-013 增量契约；目标列与来源同构，供 GATE-012 对齐）
SOURCE_PROFILE_COLUMNS: list[dict[str, object]] = [
    {"name": "id", "type": "bigint", "nullable": False, "is_primary_key": True},
    {"name": "amount", "type": "numeric", "nullable": True, "is_primary_key": False},
    {"name": "updated_at", "type": "timestamptz", "nullable": True, "is_primary_key": False},
]
SINK_PROFILE_COLUMNS: list[dict[str, object]] = [
    {"name": "id", "type": "bigint", "nullable": False, "is_primary_key": True},
    {"name": "amount", "type": "numeric", "nullable": True, "is_primary_key": False},
    {"name": "updated_at", "type": "timestamptz", "nullable": True, "is_primary_key": False},
]


def _profile_columns(columns: list[dict[str, object]]) -> dict[str, object]:
    """构造 `data_profiles.columns` 的包裹结构（S4 吸收 4）。"""
    return {
        "approximate_row_count": 1000,
        "columns": [
            {
                **column,
                "null_rate": 0.0,
                "distinct_estimate": 100,
                "classification": "UNKNOWN",
                "effective_classification": "CONFIDENTIAL",
            }
            for column in columns
        ],
    }


@pytest.fixture
async def seeded_profiles(
    db_session: AsyncSession, seeded: SeededWorkspace, seeded_connection: tuple
) -> dict[str, uuid.UUID]:
    """项目 A 下的来源 / 目标 Profile（直接建行，避免依赖真实库表的探查通道）。

    返回 `{"source": id, "sink": id}`；两者共用同一连接（生成期只读结构，不做数据搬运）。
    """
    connection, _ = seeded_connection
    expires_at = datetime.now(UTC) + timedelta(hours=24)
    source = DataProfile(
        connection_id=connection.id,
        endpoint_role="SOURCE",
        schema_name="public",
        table_name="orders",
        columns=_profile_columns(SOURCE_PROFILE_COLUMNS),
        masked_samples=[],
        classification_report_sha256="a" * 64,
        expires_at=expires_at,
    )
    sink = DataProfile(
        connection_id=connection.id,
        endpoint_role="SINK",
        schema_name="public",
        table_name="orders_wide",
        columns=_profile_columns(SINK_PROFILE_COLUMNS),
        masked_samples=[],
        classification_report_sha256="b" * 64,
        expires_at=expires_at,
    )
    db_session.add_all([source, sink])
    await db_session.flush()
    return {"source": source.id, "sink": sink.id}


@pytest.fixture
async def seeded_pipeline_version(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> uuid.UUID:
    """项目 A 下的 DRAFT 版本（生成目标；S5 不做版本链管理，S6 接管）。"""
    pipeline = Pipeline(project_id=seeded.project_a_id, name=f"pipe-{uuid.uuid4().hex[:8]}")
    db_session.add(pipeline)
    await db_session.flush()
    version = PipelineVersion(pipeline_id=pipeline.id, version_number=1, status="DRAFT")
    db_session.add(version)
    await db_session.flush()
    return version.id


# S6 版本链 / Harness 夹具（M-H / M-J）
S6_TEST_PLAN: dict[str, object] = {
    "pipeline_name": "orders_wide",
    "source": {"connector_type": "POSTGRESQL", "connection_id": "src", "table": "orders"},
    "sink": {
        "connector_type": "POSTGRESQL",
        "connection_id": "sink",
        "table": "orders_wide",
        "target_schema": {"columns": [{"name": "id", "type": "bigint"}]},
    },
    "quality_contract": {
        # T-SHADOW-02 命名统一：有效 sink 必须是 `<目标表>_shadow`（与 execution.shadow
        # 的派生名同源），否则发布链路会把 swap 指向不存在的表
        "valid_sink": "orders_wide_shadow",
        "error_sink": "orders_wide_error",
        # T-QUALITY-01 / fix-final-gate：与 `llm/mock_provider.py` 同口径显式声明阈值。
        # 此前**两个**测试夹具都没有 `max_reject_rate` → 契约走 fail-open，
        # 拒绝率 10% 也能判 PASSED（`test_consume_outbox_happy_path` 正是靠这个活的）。
        "max_reject_rate": 0.05,
        "routing": [{"rule": "not_null_id", "sink": "orders_wide_shadow"}],
        "traceability": {
            "source_row_id_column": "id",
            "error_code_column": "error_code",
            "reason_column": "error_reason",
        },
        "predicates": [
            {"name": "not_null_id", "expression": "id IS NOT NULL", "code": "QUALITY_NOT_NULL_ID"}
        ],
    },
}

S6_TEST_HOCON = (
    "env {\n"
    '  job.mode = "BATCH"\n'
    "}\n"
    "sink {\n"
    "  Jdbc {\n"
    '    password = "${SINK_PASSWORD}"\n'
    "  }\n"
    "}\n"
)


@pytest.fixture
async def frozen_version(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> tuple[uuid.UUID, str]:
    """项目 A 下**已冻结制品**的版本（VALIDATED + artifact_sha256）。

    返回 `(version_id, artifact_sha256)`：Harness Prepare 的前置状态即由此产生。
    """
    from etl_agent.versions.artifacts import freeze_version_artifacts
    from etl_agent.versions.service import mark_generated

    pipeline = Pipeline(project_id=seeded.project_a_id, name=f"pipe-{uuid.uuid4().hex[:8]}")
    db_session.add(pipeline)
    await db_session.flush()
    version = PipelineVersion(pipeline_id=pipeline.id, version_number=1, status="DRAFT")
    db_session.add(version)
    await db_session.flush()

    await mark_generated(db_session, version.id)
    digest = await freeze_version_artifacts(
        db_session, version.id, S6_TEST_PLAN, S6_TEST_HOCON
    )
    return version.id, digest


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


# ============================================================
# S7：执行 / 调度 / 可观测用例的共享夹具
# ============================================================


@pytest.fixture
async def seeded_execution(
    db_session: AsyncSession,
    seeded: SeededWorkspace,
    seeded_connection: tuple,
    frozen_version: tuple[uuid.UUID, str],
) -> SeededExecution:
    """构造「已授权、待执行」的完整事实链（不经过 HTTP，便于 Worker 用例直接驱动）。

    事实链与 `CommitService` 的产出同构（SPEC 5.2 / FR-33/35）：
    准备单 COMMITTED → 单次 Capability（库中只存 token_sha256）→ ExecutionRun(QUEUED)
    → Outbox(PENDING) → 令牌本体写入交接通道。

    令牌本体只存在于返回值与交接通道，业务库中只有摘要——这正是 AC-02/AC-05 的红线，
    故夹具不允许「把令牌塞进 payload」的写法。
    """
    from etl_agent.harness.capability.issuer import CapabilityClaims, CapabilityIssuer
    from etl_agent.harness.side_effects.registry import MAIN_EXECUTION_COMMAND
    from etl_agent.infrastructure.models import (
        ExecutionPreparation,
        ExecutionRun,
        HarnessCapabilityGrant,
        HarnessCommandOutbox,
        HarnessPolicyDecision,
    )
    from etl_agent.worker.capability_handoff import InMemoryCapabilityHandoff

    connection, _settings = seeded_connection
    version_id, artifact_sha256 = frozen_version
    project_id = seeded.project_a_id

    decision = HarnessPolicyDecision(
        project_id=project_id,
        outcome="ALLOW",
        risk_level="P3",
        rule_evidence=[],
        policy_package_id="test-package",
        policy_package_sha256="0" * 64,
    )
    db_session.add(decision)
    await db_session.flush()

    contract = {"max_records": 1000, "warning_ratio": 0.8}
    preparation = ExecutionPreparation(
        project_id=project_id,
        policy_decision_id=decision.id,
        pipeline_version_id=version_id,
        status="COMMITTED",
        risk_level="P3",
        environment="dev",
        tool_name="pipeline.execute",
        tool_version="1.0.0",
        artifact_sha256=artifact_sha256,
        input_fingerprint="a" * 64,
        resource_fingerprint="b" * 64,
        impact_report={"requested_input": {"side_effect_type": None, "resource_ids": []}},
        rollback_plan={},
        supervision_contract=contract,
        required_roles=[],
        required_approvals=0,
        idempotency_key=f"s7-{uuid.uuid4().hex[:8]}",
        requested_by=seeded.engineer_a_user_id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(preparation)
    await db_session.flush()

    issuer = CapabilityIssuer(make_settings().harness_signing_key_hex)
    claims = CapabilityClaims(
        tool_name="pipeline.execute",
        tool_version="1.0.0",
        subject=str(seeded.engineer_a_user_id),
        environment="dev",
        artifact_sha256=artifact_sha256,
        supervision_contract_sha256="c" * 64,
        classification_report_sha256="d" * 64,
        allowed_mitigations=["CANCEL", "PAUSE"],
        project_id=str(project_id),
        preparation_id=str(preparation.id),
        pipeline_version_id=str(version_id),
    )
    capability = issuer.issue(claims, now=datetime.now(UTC), ttl_seconds=300)

    run = ExecutionRun(
        pipeline_version_id=version_id,
        harness_preparation_id=preparation.id,
        idempotency_key=f"commit-{preparation.id}",
        status="QUEUED",
        trigger="MANUAL",
        supervision_contract_sha256="c" * 64,
    )
    db_session.add(run)
    await db_session.flush()

    db_session.add(
        HarnessCapabilityGrant(
            token_id=capability.token_id,
            preparation_id=preparation.id,
            run_id=run.id,
            token_sha256=capability.token_sha256,
            claims=capability.claims,
            expires_at=capability.expires_at,
        )
    )
    outbox = HarnessCommandOutbox(
        project_id=project_id,
        command_type=MAIN_EXECUTION_COMMAND,
        payload={
            "project_id": str(project_id),
            "preparation_id": str(preparation.id),
            "run_id": str(run.id),
            "token_id": str(capability.token_id),
            "claims": capability.claims,
            "allowed_mitigations": capability.claims["allowed_mitigations"],
        },
        status="PENDING",
        retry_count=0,
    )
    db_session.add(outbox)
    await db_session.flush()

    handoff = InMemoryCapabilityHandoff()
    await handoff.publish(capability.token_id, capability.token, 300)

    plan: dict[str, object] = {
        "pipeline_name": "orders_wide",
        "source": {"connector_type": "POSTGRESQL", "connection_id": str(connection.id), "table": "orders"},
        "sink": {
            "connector_type": "POSTGRESQL",
            "connection_id": str(connection.id),
            "table": "orders_wide",
            # T-SHADOW-01：错误表 DDL 需要目标列清单（生成侧恒声明，见
            # mock_provider.compliant_plan 的 sink.target_schema）
            "target_schema": {"columns": [{"name": "id", "type": "bigint"}]},
        },
        "quality_contract": {
            "valid_sink": "orders_wide_shadow",
            "error_sink": "orders_wide_error",
            "predicates": [{"name": "not_null_id", "expression": "id IS NOT NULL"}],
        },
    }
    return SeededExecution(
        project_id=project_id,
        version_id=version_id,
        preparation_id=preparation.id,
        run_id=run.id,
        outbox_id=outbox.id,
        token_id=capability.token_id,
        token=capability.token,
        connection_id=connection.id,
        plan=plan,
        hocon=S7_TEST_HOCON,
        handoff=handoff,
    )

