"""凭据轮换状态机测试（T-06.1/4 / FR-07，S3 提示词 10）。

覆盖：5 态全部可达（VALIDATING→WAITING_APPROVAL→APPROVED→ACTIVATING→SUCCEEDED）、
激活前旧凭据可用、回滚恢复旧凭据原始年龄（不刷新）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import DataConnection, EncryptedSecret
from etl_agent.secrets import rotation as rotation_service
from etl_agent.secrets import rotation_rollback as rollback_service
from etl_agent.secrets.errors import SecretStateConflict
from tests.helpers import SeededWorkspace, dev_pg_credentials, make_settings


async def _create_validated_rotation(
    db_session: AsyncSession, seeded: SeededWorkspace, connection: DataConnection
):
    """创建并用真实（开发库）凭据验证一轮轮换，返回 (rotation, settings)。"""
    settings = make_settings()
    pg = dev_pg_credentials()
    rotation = await rotation_service.create_rotation(
        db_session,
        project_id=seeded.project_a_id,
        connection_id=connection.id,
        new_username=str(pg["username"]),
        new_password=str(pg["password"]),
        reason="scheduled rotation",
        requested_by=seeded.admin_a_user_id,
        settings=settings,
    )
    rotation = await rotation_service.validate_rotation(
        db_session, project_id=seeded.project_a_id, rotation_id=rotation.id, settings=settings
    )
    return rotation, settings


async def test_rotation_state_machine(
    db_session: AsyncSession, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """5 态全部可达；激活前连接仍持旧凭据（FR-07）。"""
    connection, _ = seeded_connection
    old_secret_id = connection.secret_ref

    rotation, _ = await _create_validated_rotation(db_session, seeded, connection)
    assert rotation.status == "WAITING_APPROVAL"

    rotation = await rotation_service.approve_rotation(
        db_session,
        project_id=seeded.project_a_id,
        rotation_id=rotation.id,
        approved_by=seeded.admin_a_user_id,
    )
    assert rotation.status == "APPROVED"

    # 激活前旧凭据仍可用：连接引用未变
    await db_session.refresh(connection)
    assert connection.secret_ref == old_secret_id

    rotation = await rotation_service.begin_activation(
        db_session, project_id=seeded.project_a_id, rotation_id=rotation.id
    )
    assert rotation.status == "ACTIVATING"
    rotation = await rotation_service.finish_activation(
        db_session, project_id=seeded.project_a_id, rotation_id=rotation.id
    )
    assert rotation.status == "SUCCEEDED"
    await db_session.refresh(connection)
    assert connection.secret_ref == rotation.new_secret_ref
    assert connection.secret_ref != old_secret_id


async def test_rotation_validate_failure_marks_failed(
    db_session: AsyncSession, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """暂存凭据连通性失败 → FAILED 且连接仍持旧凭据（FR-07）。"""
    connection, _ = seeded_connection
    old_secret_id = connection.secret_ref
    settings = make_settings()
    rotation = await rotation_service.create_rotation(
        db_session,
        project_id=seeded.project_a_id,
        connection_id=connection.id,
        new_username="etl_agent",
        new_password="definitely-wrong-password-xyz",
        reason="bad credential",
        requested_by=seeded.admin_a_user_id,
        settings=settings,
    )
    assert rotation.status == "VALIDATING"
    rotation = await rotation_service.validate_rotation(
        db_session, project_id=seeded.project_a_id, rotation_id=rotation.id, settings=settings
    )
    assert rotation.status == "FAILED"
    assert rotation.failure_reason is not None
    assert "definitely-wrong-password-xyz" not in rotation.failure_reason
    await db_session.refresh(connection)
    assert connection.secret_ref == old_secret_id


async def test_rotation_rollback_restores_age(
    db_session: AsyncSession, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """回滚：旧凭据原样保留，created_at 不变 → 原始年龄不刷新（FR-07 / FR-10 口径）。"""
    connection, _ = seeded_connection
    old_secret_id = connection.secret_ref
    old_created_at = (
        await db_session.execute(
            select(EncryptedSecret.created_at).where(EncryptedSecret.id == old_secret_id)
        )
    ).scalar_one()

    rotation, _ = await _create_validated_rotation(db_session, seeded, connection)
    rotation = await rotation_service.approve_rotation(
        db_session,
        project_id=seeded.project_a_id,
        rotation_id=rotation.id,
        approved_by=seeded.admin_a_user_id,
    )
    rollback = await rollback_service.rollback_rotation(
        db_session,
        project_id=seeded.project_a_id,
        rotation_id=rotation.id,
        requested_by=seeded.admin_a_user_id,
        reason="change window cancelled",
    )
    assert rollback.status == "COMPLETED"

    # 旧凭据仍在使用且时间戳未被动过（年龄不刷新）
    await db_session.refresh(connection)
    assert connection.secret_ref == old_secret_id
    current_created_at = (
        await db_session.execute(
            select(EncryptedSecret.created_at).where(EncryptedSecret.id == old_secret_id)
        )
    ).scalar_one()
    assert current_created_at == old_created_at


async def test_rotation_state_guard_rejects_skip(
    db_session: AsyncSession, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """状态守卫：未验证直接审批 → SecretStateConflict（FR-07 不可跳跃）。"""
    connection, _ = seeded_connection
    settings = make_settings()
    rotation = await rotation_service.create_rotation(
        db_session,
        project_id=seeded.project_a_id,
        connection_id=connection.id,
        new_username="etl_agent",
        new_password="irrelevant-but-valid-length",
        reason=None,
        requested_by=seeded.admin_a_user_id,
        settings=settings,
    )
    with pytest.raises(SecretStateConflict):
        await rotation_service.approve_rotation(
            db_session,
            project_id=seeded.project_a_id,
            rotation_id=rotation.id,
            approved_by=seeded.admin_a_user_id,
        )
