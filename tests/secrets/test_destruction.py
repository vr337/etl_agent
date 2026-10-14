"""凭据销毁资格判定测试（T-06.3 / FR-09，S3 提示词 10）。

覆盖：轮换源（旧凭据）可销毁、回滚暂存（未激活）可销毁、在用凭据拒绝。
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.secrets import destruction as destruction_service
from etl_agent.secrets import rotation as rotation_service
from etl_agent.secrets import rotation_rollback as rollback_service
from tests.helpers import SeededWorkspace, dev_pg_credentials, make_settings


async def test_destruction_eligibility(
    db_session: AsyncSession, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """三类目标：在用凭据拒绝 / 回滚暂存可销毁 / 轮换源可销毁（FR-09）。"""
    connection, _ = seeded_connection
    settings = make_settings()
    pg = dev_pg_credentials()

    # 1) 在用凭据 → 不可销毁
    in_use = await destruction_service.request_destruction(
        db_session,
        project_id=seeded.project_a_id,
        connection_id=connection.id,
        secret_id=connection.secret_ref,  # type: ignore[arg-type]
        target_type="ROTATION_SOURCE",
        reason="should be rejected",
        requested_by=seeded.admin_a_user_id,
        settings=settings,
    )
    assert in_use.eligible is False
    assert in_use.status == "REJECTED"
    assert in_use.eligibility_note is not None and "在用" in in_use.eligibility_note

    # 2) 回滚暂存（未激活即中止）→ 可销毁
    rotation = await rotation_service.create_rotation(
        db_session,
        project_id=seeded.project_a_id,
        connection_id=connection.id,
        new_username=str(pg["username"]),
        new_password=str(pg["password"]),
        reason="will be rolled back",
        requested_by=seeded.admin_a_user_id,
        settings=settings,
    )
    await rotation_service.validate_rotation(
        db_session,
        project_id=seeded.project_a_id,
        rotation_id=rotation.id,
        settings=settings,
    )
    await rollback_service.rollback_rotation(
        db_session,
        project_id=seeded.project_a_id,
        rotation_id=rotation.id,
        requested_by=seeded.admin_a_user_id,
        reason="abandon",
    )
    staged = await destruction_service.request_destruction(
        db_session,
        project_id=seeded.project_a_id,
        connection_id=connection.id,
        secret_id=rotation.new_secret_ref,  # type: ignore[arg-type]
        target_type="ROLLBACK_STAGED",
        reason="cleanup staged",
        requested_by=seeded.admin_a_user_id,
        settings=settings,
    )
    assert staged.eligible is True
    assert staged.status == "PENDING"

    # 3) 执行销毁 → 墓碑；read 抛 SecretDestroyed
    executed = await destruction_service.execute_destruction(
        db_session,
        project_id=seeded.project_a_id,
        connection_id=connection.id,
        destruction_id=staged.id,
        settings=settings,
    )
    assert executed.status == "DESTROYED"
    assert executed.destroyed_at is not None
    from etl_agent.secrets.errors import SecretDestroyed
    from etl_agent.secrets.factory import create_provider

    provider = create_provider(db_session, settings)
    with pytest.raises(SecretDestroyed):
        await provider.read(rotation.new_secret_ref)  # type: ignore[arg-type]


async def test_destruction_rotation_source_after_success(
    db_session: AsyncSession, seeded: SeededWorkspace, seeded_connection: tuple
) -> None:
    """轮换成功后，被替换的旧凭据可作为轮换源销毁（FR-09）。"""
    connection, _ = seeded_connection
    settings = make_settings()
    pg = dev_pg_credentials()
    old_secret_id = connection.secret_ref

    rotation = await rotation_service.create_rotation(
        db_session,
        project_id=seeded.project_a_id,
        connection_id=connection.id,
        new_username=str(pg["username"]),
        new_password=str(pg["password"]),
        reason="rotate",
        requested_by=seeded.admin_a_user_id,
        settings=settings,
    )
    await rotation_service.validate_rotation(
        db_session, project_id=seeded.project_a_id, rotation_id=rotation.id, settings=settings
    )
    await rotation_service.approve_rotation(
        db_session,
        project_id=seeded.project_a_id,
        rotation_id=rotation.id,
        approved_by=seeded.admin_a_user_id,
    )
    await rotation_service.begin_activation(
        db_session, project_id=seeded.project_a_id, rotation_id=rotation.id
    )
    await rotation_service.finish_activation(
        db_session, project_id=seeded.project_a_id, rotation_id=rotation.id
    )

    source = await destruction_service.request_destruction(
        db_session,
        project_id=seeded.project_a_id,
        connection_id=connection.id,
        secret_id=old_secret_id,  # type: ignore[arg-type]
        target_type="ROTATION_SOURCE",
        reason="old credential retired",
        requested_by=seeded.admin_a_user_id,
        settings=settings,
    )
    assert source.eligible is True
    assert source.status == "PENDING"
