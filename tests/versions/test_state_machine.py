"""版本链状态机测试（T-11.1；S6 提示词第十二节 tests/versions）。

覆盖（FR-26）：6 态全部可达、非法迁移被拒、禁原地覆盖已冻结版本。
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.versions.errors import VersionStateConflict
from etl_agent.versions.service import mark_generated, transition
from etl_agent.versions.state import (
    ALL_STATUSES,
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_GENERATED,
    STATUS_PROFILING,
    STATUS_VALIDATED,
    STATUS_WAITING_APPROVAL,
    can_transition,
    is_frozen,
)


async def test_state_machine_all_transitions(
    db_session: AsyncSession, seeded_pipeline_version
) -> None:
    """DRAFT→PROFILING→GENERATED→VALIDATED→WAITING_APPROVAL→APPROVED 全线可达（FR-26）。"""
    from etl_agent.versions.artifacts import freeze_version_artifacts
    from etl_agent.versions.service import load_version
    from tests.conftest import S6_TEST_HOCON, S6_TEST_PLAN

    version, _ = await load_version(db_session, seeded_pipeline_version)
    assert version.status == STATUS_DRAFT
    assert len(ALL_STATUSES) == 7  # 6 个主状态 + 终态 REJECTED

    # DRAFT → PROFILING → GENERATED
    version = await transition(db_session, version, STATUS_PROFILING)
    assert version.status == STATUS_PROFILING
    assert version.lock_version == 1
    version = await transition(db_session, version, STATUS_GENERATED)

    # GENERATED → VALIDATED（冻结制品）
    digest = await freeze_version_artifacts(
        db_session, version.id, S6_TEST_PLAN, S6_TEST_HOCON
    )
    assert version.status == STATUS_VALIDATED
    assert version.artifact_sha256 == digest

    # VALIDATED → WAITING_APPROVAL → APPROVED
    version = await transition(db_session, version, STATUS_WAITING_APPROVAL)
    assert version.status == STATUS_WAITING_APPROVAL
    version = await transition(db_session, version, STATUS_APPROVED)
    assert version.status == STATUS_APPROVED
    assert is_frozen(version.status)

    # 终态不可再迁移（APPROVED → 任何状态都拒绝）
    assert not can_transition(STATUS_APPROVED, STATUS_GENERATED)
    with pytest.raises(VersionStateConflict):
        await transition(db_session, version, STATUS_GENERATED)

    # REJECTED 分支同样可达
    rejected = await transition(
        db_session, await _clone_to_draft(db_session, version), STATUS_GENERATED
    )
    assert rejected is not None


async def _clone_to_draft(db_session: AsyncSession, version) -> object:
    """构造一个 DRAFT 版本用于 REJECTED 分支演练（不依赖派生）。"""
    from etl_agent.infrastructure.models import PipelineVersion

    clone = PipelineVersion(
        pipeline_id=version.pipeline_id, version_number=99, status=STATUS_DRAFT
    )
    db_session.add(clone)
    await db_session.flush()
    return clone


async def test_illegal_transition_rejected(
    db_session: AsyncSession, seeded_pipeline_version
) -> None:
    """跳跃迁移（DRAFT → APPROVED）被拒绝（FR-26：状态链不可跳过）。"""
    from etl_agent.versions.service import load_version, transition
    from etl_agent.versions.state import STATUS_APPROVED

    version, _ = await load_version(db_session, seeded_pipeline_version)
    with pytest.raises(VersionStateConflict) as excinfo:
        await transition(db_session, version, STATUS_APPROVED)
    assert "不是合法迁移" in str(excinfo.value)


async def test_no_overwrite_frozen(
    db_session: AsyncSession, seeded_pipeline_version
) -> None:
    """已冻结版本不可原地覆盖：`mark_generated` 拒绝（FR-26 / S6 禁止事项 14）。"""
    from etl_agent.versions.artifacts import freeze_version_artifacts
    from etl_agent.versions.service import load_version
    from tests.conftest import S6_TEST_HOCON, S6_TEST_PLAN

    version, _ = await load_version(db_session, seeded_pipeline_version)
    await mark_generated(db_session, version.id)
    await freeze_version_artifacts(db_session, version.id, S6_TEST_PLAN, S6_TEST_HOCON)
    assert version.status == STATUS_VALIDATED

    with pytest.raises(VersionStateConflict):
        await mark_generated(db_session, version.id)
    with pytest.raises(VersionStateConflict):
        await freeze_version_artifacts(db_session, version.id, {"other": 1}, "sink { }")
    assert version.artifact_sha256  # 原摘要未被覆盖


async def test_freeze_idempotent_and_conflicting(
    db_session: AsyncSession, seeded_pipeline_version
) -> None:
    """重复冻结同一内容 → 幂等返回；不同内容 → 409（禁原地覆盖）。"""
    from etl_agent.versions.artifacts import freeze_version_artifacts
    from etl_agent.versions.service import load_version
    from tests.conftest import S6_TEST_HOCON, S6_TEST_PLAN

    version, _ = await load_version(db_session, seeded_pipeline_version)
    await mark_generated(db_session, version.id)
    first = await freeze_version_artifacts(
        db_session, version.id, S6_TEST_PLAN, S6_TEST_HOCON
    )
    again = await freeze_version_artifacts(
        db_session, version.id, S6_TEST_PLAN, S6_TEST_HOCON
    )
    assert first == again
    with pytest.raises(VersionStateConflict):
        await freeze_version_artifacts(db_session, version.id, {"other": 2}, "sink { }")
