"""模型与关键约束测试（T-02.5）。

两级覆盖：
- 元数据级（无需数据库）：表清单、枚举字符串化（ADR-07）、时区/大整数、唯一约束、FK、乐观锁默认值；
- 数据库级（幂等建表后回滚验证）：双唯一、幂等键、去重键、默认值、FK 强制等约束真实生效。

SPEC 9.10 建表清单共 41 张表（提示词第九节逐表列示；文中「30 张」为笔误，以清单为准）。
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import DateTime, Enum, String
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure import models
from etl_agent.infrastructure.base import Base
from etl_agent.infrastructure.database import async_session_factory, engine

EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        # M1 项目与工作区治理
        "users",
        "projects",
        "project_memberships",
        "project_role_grants",
        # M2 数据连接与凭据治理
        "data_connections",
        "encrypted_secrets",
        "secret_rotation_requests",
        "secret_migration_requests",
        "secret_rotation_rollback_requests",
        "secret_destruction_requests",
        "credential_expiry_alerts",
        "credential_alert_deliveries",
        # M3 元数据探查与文件资产
        "data_profiles",
        "file_assets",
        # M4-M5 生成与版本
        "pipelines",
        "pipeline_versions",
        "pipeline_artifacts",
        "agent_runs",
        "approval_requests",
        "pipeline_schedules",
        # M6 Harness 安全执行内核
        "harness_policy_decisions",
        "execution_preparations",
        "harness_approval_decisions",
        "harness_capability_grants",
        "harness_command_outbox",
        "harness_side_effect_preparations",
        "harness_side_effect_approvals",
        "harness_side_effect_capability_grants",
        # M7 执行调度与数据面
        "execution_runs",
        "execution_events",
        # M8 运行时监督与受管回滚
        "runtime_supervision_snapshots",
        "quality_reports",
        "rollback_requests",
        "rollback_cleanup_requests",
        # M9 审计与评测
        "harness_evidence_streams",
        "harness_evidence_events",
        "benchmark_runs",
        "safety_improvement_proposals",
        "safety_releases",
        # M11 平台
        "worker_heartbeats",
        "audit_events",
    }
)

# 枚举语义列：必须 String 持久化（ADR-07 禁止 PG native enum）
ENUM_LIKE_COLUMNS: dict[str, tuple[str, ...]] = {
    "data_connections": ("connector_type", "status"),
    "secret_rotation_requests": ("status",),
    "secret_migration_requests": ("status", "source_provider", "target_provider"),
    "secret_rotation_rollback_requests": ("status",),
    "secret_destruction_requests": ("status", "target_type"),
    "credential_expiry_alerts": ("level",),
    "credential_alert_deliveries": ("status",),
    "data_profiles": ("endpoint_role",),
    "pipeline_versions": ("status",),
    "pipeline_artifacts": ("artifact_type",),
    "agent_runs": ("status",),
    "approval_requests": ("status", "decision"),
    "pipeline_schedules": ("mode", "misfire_policy"),
    "harness_policy_decisions": ("outcome", "risk_level"),
    "execution_preparations": ("status", "risk_level"),
    "harness_approval_decisions": ("role", "decision"),
    "harness_command_outbox": ("status",),
    "harness_side_effect_preparations": ("status", "risk_level"),
    "harness_side_effect_approvals": ("role", "decision"),
    "execution_runs": ("status", "trigger", "supervision_action"),
    "runtime_supervision_snapshots": ("action",),
    "quality_reports": ("status",),
    "rollback_requests": ("status", "artifact_status"),
    "harness_evidence_events": ("risk_level",),
    "benchmark_runs": ("status",),
    "safety_improvement_proposals": ("type", "status"),
    "safety_releases": ("stage",),
}

BIGINT_COLUMNS: dict[str, tuple[str, ...]] = {
    "file_assets": ("size_bytes", "row_count"),
    "execution_runs": ("source_rows", "sink_rows", "source_bytes", "sink_bytes"),
    "quality_reports": ("total_rows", "passed_rows", "rejected_rows"),
    "harness_evidence_events": ("sequence",),
}

UNIQUE_CONSTRAINTS: dict[str, tuple[tuple[str, ...], ...]] = {
    "users": (("subject",),),
    "project_memberships": (("project_id", "user_id"),),
    "data_connections": (("project_id", "name"),),
    "credential_expiry_alerts": (("dedupe_key",),),
    "pipeline_versions": (("pipeline_id", "version_number"),),
    "pipeline_schedules": (("pipeline_id",),),
    "agent_runs": (("thread_id",),),
    "execution_preparations": (("project_id", "idempotency_key"), ("policy_decision_id",)),
    "harness_approval_decisions": (("preparation_id", "role"), ("preparation_id", "decided_by")),
    "harness_capability_grants": (("preparation_id",), ("token_sha256",), ("run_id",)),
    "harness_side_effect_preparations": (
        ("project_id", "idempotency_key"),
        ("policy_decision_id",),
    ),
    "harness_side_effect_approvals": (
        ("preparation_id", "role"),
        ("preparation_id", "decided_by"),
    ),
    "harness_side_effect_capability_grants": (("preparation_id",), ("token_sha256",)),
    "execution_runs": (("harness_preparation_id",),),
    "runtime_supervision_snapshots": (("run_id",),),
    "quality_reports": (("run_id",),),
    "harness_evidence_events": (("stream_id", "sequence"), ("event_hash",)),
    "safety_releases": (("proposal_id",),),
}

# agent_runs 禁止字段（FR-67：Prompt 与候选正文不落业务库）
AGENT_RUN_FORBIDDEN_COLUMNS = frozenset(
    {
        "prompt",
        "requirement_text",
        "candidate_hocon",
        "candidate_body",
        "candidate_plan",
        "candidate_text",
    }
)

# Checkpoint 层表只允许存在于 etl_checkpoint 库（ADR-03）
CHECKPOINT_ONLY_TABLES = frozenset(
    {"conversation_state", "workflow_checkpoint", "seatunnel_job_binding"}
)


# ============================================================
# 元数据级（无需数据库）
# ============================================================


def test_table_inventory_matches_spec() -> None:
    """SPEC 9.10 建表清单 41 张表齐全（DoD-3）。"""
    assert set(Base.metadata.tables) == set(EXPECTED_TABLES)
    assert len(Base.metadata.tables) == len(EXPECTED_TABLES) == 41


def test_every_table_has_primary_key() -> None:
    for name, table in Base.metadata.tables.items():
        assert table.primary_key.columns, f"{name} 缺少主键"


def test_no_native_enum_columns() -> None:
    """ADR-07：全库禁止 PostgreSQL native enum（DoD-5）。"""
    for name, table in Base.metadata.tables.items():
        for column in table.columns:
            assert not isinstance(column.type, Enum), f"{name}.{column.name} 使用了 native enum"


def test_enum_like_columns_are_string() -> None:
    """枚举语义列一律 String 持久化。"""
    for table_name, columns in ENUM_LIKE_COLUMNS.items():
        table = Base.metadata.tables[table_name]
        for column_name in columns:
            assert isinstance(table.columns[column_name].type, String), (
                f"{table_name}.{column_name} 应为 String"
            )


def test_timestamps_are_timezone_aware() -> None:
    """SPEC 9.10.4：时间戳统一 TIMESTAMP WITH TIME ZONE。"""
    for name, table in Base.metadata.tables.items():
        created_at = table.columns["created_at"]
        assert isinstance(created_at.type, DateTime)
        assert created_at.type.timezone is True, f"{name}.created_at 非 timestamptz"


def test_bigint_columns() -> None:
    """SPEC 9.10.3：sequence 等大整数用 BigInteger。"""
    from sqlalchemy import BigInteger

    for table_name, columns in BIGINT_COLUMNS.items():
        table = Base.metadata.tables[table_name]
        for column_name in columns:
            assert isinstance(table.columns[column_name].type, BigInteger), (
                f"{table_name}.{column_name} 应为 BigInteger"
            )


def test_optimistic_lock_default() -> None:
    """SPEC 9.10.5：pipeline_versions.lock_version 乐观锁默认 0。"""
    column = Base.metadata.tables["pipeline_versions"].columns["lock_version"]
    assert column.default is not None and column.default.arg == 0
    assert column.server_default is not None


def test_unique_constraints_declared() -> None:
    """关键唯一约束（含四眼审批双唯一）已在元数据声明（DoD-4 元数据侧）。"""
    from sqlalchemy import UniqueConstraint

    for table_name, expected in UNIQUE_CONSTRAINTS.items():
        table = Base.metadata.tables[table_name]
        declared = {
            tuple(sorted(col.name for col in constraint.columns))
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        for columns in expected:
            assert tuple(sorted(columns)) in declared, (
                f"{table_name} 缺少唯一约束 {columns}"
            )


def test_foreign_keys_reference_existing_tables() -> None:
    """全部 FK 目标表均已注册（自引用除外）。"""
    for name, table in Base.metadata.tables.items():
        for column in table.columns:
            for fk in column.foreign_keys:
                target = fk.target_fullname.split(".")[0]
                assert target in Base.metadata.tables, f"{name}.{column.name} → {target} 未注册"


def test_agent_runs_has_no_prompt_or_candidate_body() -> None:
    """SPEC 十六.9 / FR-67：agent_runs 禁止 Prompt / 候选正文字段。"""
    columns = set(Base.metadata.tables["agent_runs"].columns)
    assert not (columns & AGENT_RUN_FORBIDDEN_COLUMNS)


def test_checkpoint_tables_not_in_business_db() -> None:
    """ADR-03：Checkpoint 层表不进入业务库元数据。"""
    assert not (set(Base.metadata.tables) & CHECKPOINT_ONLY_TABLES)


def test_project_scoped_tables_have_isolation_index() -> None:
    """SPEC 9.10.2：项目级资源带 (project_id, status|created_at) 索引。"""
    expected: dict[str, tuple[str, ...]] = {
        "data_connections": ("project_id", "status"),
        "file_assets": ("project_id", "created_at"),
        "harness_policy_decisions": ("project_id", "created_at"),
        "execution_preparations": ("project_id", "status"),
        "harness_command_outbox": ("project_id", "status"),
        "harness_side_effect_preparations": ("project_id", "status"),
        "benchmark_runs": ("project_id", "created_at"),
        "audit_events": ("project_id", "created_at"),
    }
    from sqlalchemy import Index

    for table_name, columns in expected.items():
        table = Base.metadata.tables[table_name]
        index_columns = {
            tuple(col.name for col in index.columns)
            for index in table.indexes
            if isinstance(index, Index)
        }
        assert columns in index_columns, f"{table_name} 缺少索引 {columns}"


# ============================================================
# 数据库级（幂等建表 + 回滚验证约束真实生效）
# ============================================================


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """业务库会话：幂等建表 → 交付 → 关闭即回滚，不残留数据。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_session_factory()
    try:
        yield session
    finally:
        await session.close()
        # 释放连接池：pytest-asyncio 每个测试独立事件循环，
        # 不释放会导致 asyncpg 连接跨循环复用报 "Event loop is closed"。
        await engine.dispose()


def _now() -> datetime:
    return datetime.now(UTC)


async def _make_project(session: AsyncSession) -> models.Project:
    project = models.Project(name=f"proj-{uuid.uuid4()}")
    session.add(project)
    await session.flush()
    return project


async def _make_user(session: AsyncSession) -> models.User:
    user = models.User(subject=f"sub-{uuid.uuid4()}", display_name="测试用户")
    session.add(user)
    await session.flush()
    return user


async def _make_pipeline_version(
    session: AsyncSession, project: models.Project
) -> models.PipelineVersion:
    pipeline = models.Pipeline(project_id=project.id, name=f"pipe-{uuid.uuid4()}")
    session.add(pipeline)
    await session.flush()
    version = models.PipelineVersion(
        pipeline_id=pipeline.id, version_number=1, status="DRAFT"
    )
    session.add(version)
    await session.flush()
    return version


async def _make_preparation(
    session: AsyncSession,
    project: models.Project,
    version: models.PipelineVersion,
    *,
    idempotency_key: str | None = None,
) -> models.ExecutionPreparation:
    decision = models.HarnessPolicyDecision(
        project_id=project.id, outcome="REQUIRE_APPROVAL", risk_level="P1"
    )
    session.add(decision)
    await session.flush()
    preparation = models.ExecutionPreparation(
        project_id=project.id,
        policy_decision_id=decision.id,
        pipeline_version_id=version.id,
        status="WAITING_APPROVAL",
        risk_level="P1",
        idempotency_key=idempotency_key or f"idem-{uuid.uuid4()}",
        requested_by=uuid.uuid4(),
    )
    session.add(preparation)
    await session.flush()
    return preparation


async def test_users_subject_unique_conflict(db_session: AsyncSession) -> None:
    """users.subject 唯一约束生效。"""
    subject = f"sub-{uuid.uuid4()}"
    db_session.add(models.User(subject=subject, display_name="甲"))
    await db_session.flush()
    db_session.add(models.User(subject=subject, display_name="乙"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_project_membership_pair_unique_conflict(db_session: AsyncSession) -> None:
    """(project_id, user_id) 唯一约束生效（FR-02）。"""
    project = await _make_project(db_session)
    user = await _make_user(db_session)
    db_session.add(
        models.ProjectMembership(project_id=project.id, user_id=user.id, role="DATA_ENGINEER")
    )
    await db_session.flush()
    db_session.add(
        models.ProjectMembership(project_id=project.id, user_id=user.id, role="VIEWER")
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_pipeline_version_number_unique_conflict(db_session: AsyncSession) -> None:
    """(pipeline_id, version_number) 唯一约束生效。"""
    project = await _make_project(db_session)
    pipeline = models.Pipeline(project_id=project.id, name=f"pipe-{uuid.uuid4()}")
    db_session.add(pipeline)
    await db_session.flush()
    db_session.add(models.PipelineVersion(pipeline_id=pipeline.id, version_number=1, status="DRAFT"))
    await db_session.flush()
    db_session.add(
        models.PipelineVersion(pipeline_id=pipeline.id, version_number=1, status="GENERATED")
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_optimistic_lock_version_defaults_to_zero(db_session: AsyncSession) -> None:
    """lock_version 插入默认 0（SPEC 9.10.5）。"""
    version = await _make_pipeline_version(db_session, await _make_project(db_session))
    assert version.lock_version == 0


async def test_data_connection_credential_defaults(db_session: AsyncSession) -> None:
    """凭据账龄默认 90 / 预警 14（FR-11）。"""
    project = await _make_project(db_session)
    connection = models.DataConnection(
        project_id=project.id,
        name=f"conn-{uuid.uuid4()}",
        connector_type="MYSQL",
        status="UNTESTED",
    )
    db_session.add(connection)
    await db_session.flush()
    assert connection.credential_max_age_days == 90
    assert connection.credential_warning_days == 14


async def test_approval_role_slot_unique_conflict(db_session: AsyncSession) -> None:
    """四眼审批 (preparation_id, role) 唯一：同一职责槽位只能一人（FR-32 / AC-01）。"""
    project = await _make_project(db_session)
    version = await _make_pipeline_version(db_session, project)
    preparation = await _make_preparation(db_session, project, version)
    db_session.add(
        models.HarnessApprovalDecision(
            preparation_id=preparation.id,
            role="DATA_APPROVER",
            decided_by=uuid.uuid4(),
            decision="APPROVE",
        )
    )
    await db_session.flush()
    db_session.add(
        models.HarnessApprovalDecision(
            preparation_id=preparation.id,
            role="DATA_APPROVER",
            decided_by=uuid.uuid4(),
            decision="REJECT",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_approval_decided_by_unique_conflict(db_session: AsyncSession) -> None:
    """四眼审批 (preparation_id, decided_by) 唯一：申请人禁止自批、一人只能占一槽。"""
    project = await _make_project(db_session)
    version = await _make_pipeline_version(db_session, project)
    preparation = await _make_preparation(db_session, project, version)
    approver = uuid.uuid4()
    db_session.add(
        models.HarnessApprovalDecision(
            preparation_id=preparation.id,
            role="DATA_APPROVER",
            decided_by=approver,
            decision="APPROVE",
        )
    )
    await db_session.flush()
    db_session.add(
        models.HarnessApprovalDecision(
            preparation_id=preparation.id,
            role="SECURITY_APPROVER",
            decided_by=approver,
            decision="APPROVE",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_preparation_idempotency_key_unique_within_project(
    db_session: AsyncSession,
) -> None:
    """准备单幂等键项目内唯一（FR-31）。"""
    project = await _make_project(db_session)
    version = await _make_pipeline_version(db_session, project)
    await _make_preparation(db_session, project, version, idempotency_key="idem-same")
    db_session.add(
        models.HarnessPolicyDecision(
            project_id=project.id, outcome="ALLOW", risk_level="P2"
        )
    )
    await db_session.flush()
    # 同项目同幂等键 → 冲突（换一个 policy_decision 以隔离其他唯一约束）
    decision = models.HarnessPolicyDecision(
        project_id=project.id, outcome="ALLOW", risk_level="P2"
    )
    db_session.add(decision)
    await db_session.flush()
    db_session.add(
        models.ExecutionPreparation(
            project_id=project.id,
            policy_decision_id=decision.id,
            pipeline_version_id=version.id,
            status="READY",
            risk_level="P2",
            idempotency_key="idem-same",
            requested_by=uuid.uuid4(),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_capability_grant_preparation_unique_conflict(db_session: AsyncSession) -> None:
    """能力令牌与准备单 1:1（FR-33）。"""
    project = await _make_project(db_session)
    version = await _make_pipeline_version(db_session, project)
    preparation = await _make_preparation(db_session, project, version)
    for _ in range(2):
        db_session.add(
            models.HarnessCapabilityGrant(
                token_id=uuid.uuid4(),
                preparation_id=preparation.id,
                token_sha256=f"{uuid.uuid4().hex}{uuid.uuid4().hex}"[:64],
                expires_at=_now(),
            )
        )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_evidence_event_sequence_unique_conflict(db_session: AsyncSession) -> None:
    """证据事件 (stream_id, sequence) 唯一（FR-36）。"""
    project = await _make_project(db_session)
    stream = models.HarnessEvidenceStream(project_id=project.id)
    db_session.add(stream)
    await db_session.flush()
    for event_hash in (uuid.uuid4().hex + uuid.uuid4().hex, uuid.uuid4().hex + uuid.uuid4().hex):
        db_session.add(
            models.HarnessEvidenceEvent(
                stream_id=stream.id,
                sequence=1,
                payload_sha256=uuid.uuid4().hex,
                event_hash=event_hash[:64],
                occurred_at=_now(),
                event_type="harness.commit",
            )
        )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_evidence_event_hash_unique_conflict(db_session: AsyncSession) -> None:
    """证据事件 event_hash 全局唯一（哈希链防篡改）。"""
    project = await _make_project(db_session)
    stream = models.HarnessEvidenceStream(project_id=project.id)
    db_session.add(stream)
    await db_session.flush()
    same_hash = (uuid.uuid4().hex + uuid.uuid4().hex)[:64]
    for sequence in (1, 2):
        db_session.add(
            models.HarnessEvidenceEvent(
                stream_id=stream.id,
                sequence=sequence,
                payload_sha256=uuid.uuid4().hex,
                event_hash=same_hash,
                occurred_at=_now(),
                event_type="harness.commit",
            )
        )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_credential_alert_dedupe_key_unique_conflict(db_session: AsyncSession) -> None:
    """凭据预警去重键唯一（FR-10）。"""
    project = await _make_project(db_session)
    connection = models.DataConnection(
        project_id=project.id,
        name=f"conn-{uuid.uuid4()}",
        connector_type="MYSQL",
        status="READY",
    )
    db_session.add(connection)
    await db_session.flush()
    for _ in range(2):
        db_session.add(
            models.CredentialExpiryAlert(
                connection_id=connection.id, level="WARNING", dedupe_key="dedupe-same"
            )
        )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_execution_run_defaults_and_fk_enforcement(db_session: AsyncSession) -> None:
    """execution_runs 默认值（supervision_action=CONTINUE / attempt_number=1）与 FK 强制。"""
    project = await _make_project(db_session)
    version = await _make_pipeline_version(db_session, project)
    preparation = await _make_preparation(db_session, project, version)
    run = models.ExecutionRun(
        pipeline_version_id=version.id,
        harness_preparation_id=preparation.id,
        status="QUEUED",
        trigger="MANUAL",
    )
    db_session.add(run)
    await db_session.flush()
    assert run.supervision_action == "CONTINUE"
    assert run.attempt_number == 1

    # FK 强制：引用不存在的 pipeline_version → IntegrityError
    db_session.add(
        models.ExecutionRun(
            pipeline_version_id=uuid.uuid4(),
            harness_preparation_id=uuid.uuid4(),
            status="QUEUED",
            trigger="MANUAL",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
