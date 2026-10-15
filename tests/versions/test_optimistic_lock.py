"""乐观锁测试（T-11.3；S6 提示词第十二节 tests/versions）。

覆盖（FR-26 / SPEC 8「高可用」）：并发更新同一版本 → `BIZ_409_STATE_CONFLICT`；
`update_with_lock` 每次成功都把 `lock_version` +1。
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.versions.errors import VersionStateConflict
from etl_agent.versions.service import load_version, update_with_lock


async def test_optimistic_lock(
    db_session: AsyncSession, seeded_pipeline_version
) -> None:
    """错版本的 lock_version 更新被拒（rowcount=0 → 409）。"""
    version, _ = await load_version(db_session, seeded_pipeline_version)
    current = version.lock_version

    # 第一次更新成功：lock_version +1
    updated = await update_with_lock(db_session, version, status="PROFILING")
    assert updated.lock_version == current + 1

    # 用过期（陈旧）的 lock_version 再更新 → 并发冲突
    with pytest.raises(VersionStateConflict) as excinfo:
        await update_with_lock(
            db_session, version, expected_lock_version=current, status="GENERATED"
        )
    assert "并发修改" in str(excinfo.value)
    assert version.status == "PROFILING"  # 未被陈旧写入污染


async def test_optimistic_lock_field_update(
    db_session: AsyncSession, seeded_pipeline_version
) -> None:
    """字段更新走乐观锁且版本计数递增（供后续并发保护复用）。"""
    version, _ = await load_version(db_session, seeded_pipeline_version)
    before = version.lock_version
    updated = await update_with_lock(db_session, version, artifact_sha256="a" * 64)
    assert updated.lock_version == before + 1
    assert updated.artifact_sha256 == "a" * 64
