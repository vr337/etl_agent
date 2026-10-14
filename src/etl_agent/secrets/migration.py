"""M-C 凭据治理 — Provider 迁移（T-06.2 / FR-08）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / ADR-05
覆盖 FR：FR-08（fernet-db ↔ vault-kv-v2 迁移；目标 UUID 申请时冻结）
对应 AC：AC-02（迁移过程密钥材料不落日志/响应）

「目标 UUID 申请时冻结」的实现：create 阶段即分配 target_secret_id 并落库，
后续 stage/activate 都写/切这同一个 id——重试不会产生孤立秘密（FR-08）。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.config import SECRET_PROVIDERS, Settings
from etl_agent.connections.service import load_connection
from etl_agent.infrastructure.models import DataConnection, EncryptedSecret, SecretMigrationRequest
from etl_agent.secrets.errors import SecretNotFound, SecretStateConflict
from etl_agent.secrets.factory import create_provider

# 迁移状态机（S1 模型 status 为 String；SPEC 10 T-06 未细化，本模块定义）
MIGRATION_PENDING = "PENDING"
MIGRATION_STAGED = "STAGED"
MIGRATION_SUCCEEDED = "SUCCEEDED"
MIGRATION_FAILED = "FAILED"


async def _load_migration(
    session: AsyncSession, project_id: uuid.UUID, migration_id: uuid.UUID
) -> SecretMigrationRequest:
    """按项目加载迁移申请（经 connection 隔离，AC-12）。"""
    result = await session.execute(
        select(SecretMigrationRequest)
        .join(DataConnection, DataConnection.id == SecretMigrationRequest.connection_id)
        .where(
            SecretMigrationRequest.id == migration_id,
            DataConnection.project_id == project_id,
        )
    )
    migration = result.scalar_one_or_none()
    if migration is None:
        raise SecretNotFound("迁移申请不存在")
    return migration


async def create_migration(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    connection_id: uuid.UUID,
    target_provider: str,
    reason: str | None,
    requested_by: uuid.UUID,
    settings: Settings,
) -> SecretMigrationRequest:
    """创建迁移申请并冻结目标 UUID（FR-08：PENDING）。"""
    if target_provider not in SECRET_PROVIDERS:
        from etl_agent.auth.errors import ValidationError

        raise ValidationError(
            f"非法目标 Provider: {target_provider!r}，可选 {sorted(SECRET_PROVIDERS)}"
        )
    connection = await load_connection(session, project_id, connection_id)
    source_provider = await _current_provider(session, connection)
    if source_provider == target_provider:
        raise SecretStateConflict(f"连接已在使用 {target_provider}，无需迁移")
    # 目标 UUID 申请时冻结：先分配 id 再落库（重试写同一路径，避免孤立秘密，FR-08）
    frozen_target_id = uuid.uuid4()
    migration = SecretMigrationRequest(
        connection_id=connection_id,
        source_provider=source_provider,
        target_provider=target_provider,
        target_secret_id=frozen_target_id,
        status=MIGRATION_PENDING,
        requested_by=requested_by,
        reason=reason,
    )
    session.add(migration)
    await session.flush()
    return migration


async def stage_migration(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    migration_id: uuid.UUID,
    settings: Settings,
) -> SecretMigrationRequest:
    """暂存到目标 Provider（FR-08：PENDING → STAGED；幂等，重试写同一 UUID）。"""
    migration = await _load_migration(session, project_id, migration_id)
    if migration.status not in {MIGRATION_PENDING, MIGRATION_STAGED}:
        raise SecretStateConflict(f"迁移状态 {migration.status} 不可暂存")
    connection = await load_connection(session, project_id, migration.connection_id)
    if connection.secret_ref is None:
        raise SecretStateConflict("连接无可用凭据引用")
    # 从源 Provider 读明文（内存中短暂持有，禁止事项 16）
    source = create_provider(session, settings)
    plaintext = await source.read(connection.secret_ref)
    # 写到目标 Provider 的冻结 UUID（create_provider 按 target_provider 选择实现）
    target_settings = settings.model_copy(
        update={"secret_provider": migration.target_provider}
    )
    target = create_provider(session, target_settings)
    assert migration.target_secret_id is not None  # create 时已冻结
    await target.store(plaintext, secret_id=migration.target_secret_id)
    migration.status = MIGRATION_STAGED
    await session.flush()
    return migration


async def activate_migration(
    session: AsyncSession, *, project_id: uuid.UUID, migration_id: uuid.UUID
) -> SecretMigrationRequest:
    """完成迁移（FR-08：STAGED → SUCCEEDED）：切换连接到目标 Provider 的冻结 UUID。"""
    migration = await _load_migration(session, project_id, migration_id)
    if migration.status != MIGRATION_STAGED:
        raise SecretStateConflict(f"迁移状态 {migration.status} 不可激活（须先 STAGED）")
    connection = await load_connection(session, project_id, migration.connection_id)
    assert migration.target_secret_id is not None
    connection.secret_ref = migration.target_secret_id
    migration.status = MIGRATION_SUCCEEDED
    migration.completed_at = datetime.now(UTC)
    await session.flush()
    return migration


async def _current_provider(session: AsyncSession, connection: DataConnection) -> str:
    """连接当前凭据的 Provider 标识（迁移源）。"""
    if connection.secret_ref is None:
        from etl_agent.auth.errors import ValidationError

        raise ValidationError("连接尚未登记凭据，无法迁移")
    result = await session.execute(
        select(EncryptedSecret.provider).where(EncryptedSecret.id == connection.secret_ref)
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise SecretNotFound("连接凭据引用不存在")
    return provider
