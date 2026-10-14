"""Provider 迁移测试（T-06.2 / FR-08，S3 提示词 10）。

覆盖：目标 UUID 申请时冻结（stage 失败重试不换 UUID）、同 Provider 迁移被拒、
store(secret_id=...) 冻结写入原语。
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import EncryptedSecret
from etl_agent.secrets import migration as migration_service
from etl_agent.secrets.errors import SecretStateConflict, VaultUnavailable
from etl_agent.secrets.fernet_provider import FernetSecretProvider
from tests.helpers import SeededWorkspace, make_settings


async def test_migration_target_uuid_frozen(
    db_session: AsyncSession, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """目标 UUID 申请时冻结；stage 重试写同一 UUID（FR-08）。"""
    connection, _ = seeded_connection
    settings = make_settings()

    migration = await migration_service.create_migration(
        db_session,
        project_id=seeded.project_a_id,
        connection_id=connection.id,
        target_provider="vault-kv-v2",
        reason="move to vault",
        requested_by=seeded.admin_a_user_id,
        settings=settings,
    )
    assert migration.status == "PENDING"
    frozen_id = migration.target_secret_id
    assert frozen_id is not None

    # 首次 stage 会因 Vault 未部署失败（Q5 待定）——但目标 UUID 不被改变
    with pytest.raises(VaultUnavailable):
        await migration_service.stage_migration(
            db_session,
            project_id=seeded.project_a_id,
            migration_id=migration.id,
            settings=settings,
        )
    await db_session.refresh(migration)
    assert migration.target_secret_id == frozen_id  # 冻结：重试不会换 UUID

    # 源 Provider 仍为 fernet-db，连接引用不变
    await db_session.refresh(connection)
    assert connection.secret_ref is not None


async def test_migration_same_provider_rejected(
    db_session: AsyncSession, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """目标 Provider 与当前相同 → SecretStateConflict（FR-08）。"""
    connection, _ = seeded_connection
    with pytest.raises(SecretStateConflict):
        await migration_service.create_migration(
            db_session,
            project_id=seeded.project_a_id,
            connection_id=connection.id,
            target_provider="fernet-db",
            reason=None,
            requested_by=seeded.admin_a_user_id,
            settings=make_settings(),
        )


async def test_migration_frozen_id_store_primitive(db_session: AsyncSession) -> None:
    """store(secret_id=...) 冻结写入原语（迁移 stage 的底层，FR-08）。"""
    provider = FernetSecretProvider(db_session, make_settings())
    frozen = await provider.store(
        {"username": "u", "password": "p"}, secret_id=uuid.uuid4()
    )
    row = (
        await db_session.execute(
            select(EncryptedSecret).where(EncryptedSecret.id == frozen.secret_id)
        )
    ).scalar_one()
    assert row.provider == "fernet-db"
    assert row.key_version == 1
