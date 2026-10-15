"""派生版本测试（T-11.2；S6 提示词第十二节 tests/versions）。

覆盖（FR-26）：修改必须派生（`parent_version_id`），派生自己是干净 DRAFT，version_number 递增。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import PipelineVersion
from etl_agent.versions.errors import VersionNotFound
from etl_agent.versions.service import derive_version, load_version


async def test_derivation_parent_id(
    db_session: AsyncSession, frozen_version: tuple
) -> None:
    """派生版本的 parent_version_id 指向源版本且自为 DRAFT（FR-26）。"""
    source_id, _sha = frozen_version
    derived = await derive_version(db_session, source_id, project_id=(await load_version(db_session, source_id))[1])

    assert derived.parent_version_id == source_id
    assert derived.status == "DRAFT"
    assert derived.version_number == 2
    assert derived.artifact_sha256 is None  # 派生版本不带父制品（正文在 Checkpoint，S5 吸收 2）


async def test_derivation_from_terminal_version(
    db_session: AsyncSession, frozen_version: tuple
) -> None:
    """APPROVED / REJECTED 终态版本仍可派生——改内容只能走派生（FR-26）。"""
    source_id, _ = frozen_version
    _, project_id = await load_version(db_session, source_id)
    source, _ = await load_version(db_session, source_id, project_id)

    from etl_agent.versions.service import transition
    from etl_agent.versions.state import (
        STATUS_APPROVED,
        STATUS_WAITING_APPROVAL,
    )

    await transition(db_session, source, STATUS_WAITING_APPROVAL)
    await transition(db_session, source, STATUS_APPROVED)

    derived = await derive_version(db_session, source_id, project_id=project_id)
    assert derived.parent_version_id == source_id
    assert derived.status == "DRAFT"


async def test_derivation_cross_project_404(
    db_session: AsyncSession, frozen_version: tuple, seeded
) -> None:
    """跨项目派生他人版本 → 404（视同不存在，不泄露存在性）。"""
    source_id, _ = frozen_version
    import pytest


    with pytest.raises(VersionNotFound):
        await derive_version(db_session, source_id, project_id=seeded.project_b_id)

    # 数据库中未产生任何派生行
    rows = (await db_session.execute(select(PipelineVersion))).scalars().all()
    assert len(rows) == 1
