"""制品 SHA256 冻结测试（T-11.4；S6 提示词第十二节 tests/versions）。

覆盖（FR-26 / FR-67）：plan / hocon / quality_contract 摘要稳定可复现；
制品表只存摘要不存正文。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.versions.artifacts import (
    artifact_digests,
    list_artifacts,
    require_frozen,
)
from etl_agent.versions.state import STATUS_VALIDATED
from tests.helpers import SeededWorkspace


async def test_artifacts_sha256(
    db_session: AsyncSession, frozen_version: tuple
) -> None:
    """三类制品摘要稳定（同内容恒同摘要），且制品表不含正文（FR-67）。"""
    version_id, digest = frozen_version
    rows = await list_artifacts(db_session, version_id)
    digests = {row.artifact_type: row.sha256 for row in rows}
    # 三类制品各有自己的摘要；版本级摘要 = HOCON 摘要（数据面实际执行物）
    assert set(digests) == {"plan", "hocon", "quality_contract"}
    assert all(len(value) == 64 for value in digests.values())
    assert digests["hocon"] == digest

    # 摘要稳定可复现：同一内容再算一次得到同一摘要
    from etl_agent.versions.artifacts import artifact_digests
    from tests.conftest import S6_TEST_HOCON, S6_TEST_PLAN

    recomputed = artifact_digests(S6_TEST_PLAN, S6_TEST_HOCON)
    assert recomputed["hocon"] == digest

    dumped = " ".join(
        str(getattr(row, column)) for row in rows for column in ("artifact_type", "sha256")
    )
    assert "quality_contract" in dumped  # 只存类型名
    assert "job.mode" not in dumped  # 正文未落库


async def test_artifact_digest_quality_contract_defaults_in_plan(
    db_session: AsyncSession,
) -> None:
    """缺省 quality_contract 时取 `plan["quality_contract"]`（FR-27 按类型比较的前提）。"""
    plan = {"quality_contract": {"predicates": [{"name": "x"}]}}
    digests = artifact_digests(plan, "hocon { }\n")
    assert digests["quality_contract"]

    explicit = artifact_digests(plan, "hocon { }\n", {"other": 1})
    assert explicit["quality_contract"] != digests["quality_contract"]


async def test_require_frozen_rejects_unfrozen(
    db_session: AsyncSession, seeded: SeededWorkspace, seeded_pipeline_version
) -> None:
    """未冻结版本 → ArtifactNotFrozen（Prepare 前置，FR-31）。"""
    import pytest

    from etl_agent.versions.errors import ArtifactNotFrozen

    with pytest.raises(ArtifactNotFrozen):
        await require_frozen(
            db_session, seeded_pipeline_version, seeded.project_a_id
        )


async def test_frozen_version_status(
    db_session: AsyncSession, frozen_version: tuple
) -> None:
    """冻结后版本状态为 VALIDATED 且摘要非空。"""
    from etl_agent.versions.service import load_version

    version_id, digest = frozen_version
    version, _ = await load_version(db_session, version_id)
    assert version.status == STATUS_VALIDATED
    assert version.artifact_sha256 == digest
