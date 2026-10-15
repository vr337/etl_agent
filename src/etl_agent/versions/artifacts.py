"""M-H 不可变版本与设计审批 — 制品 SHA256 冻结。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-H / 第 5.2 章 pipeline_artifacts / 第 6.1 章 API-07
覆盖 FR：FR-26（制品 SHA256 冻结；版本只能派生不可原地覆盖）、FR-67（正文不落业务库）
对应 AC：AC-04（通过门禁后生成不可变版本摘要）

S5 吸收 2 决议（S6 遵循）：**正文继续在 `etl_checkpoint` 库**，`pipeline_artifacts` 只存
`{artifact_type, sha256}`。因此冻结动作只落摘要，业务库永远不含 plan / HOCON 正文。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import PipelineArtifact, PipelineVersion
from etl_agent.versions.errors import ArtifactNotFrozen, VersionNotFound, VersionStateConflict
from etl_agent.versions.service import load_version, update_with_lock
from etl_agent.versions.state import (
    STATUS_GENERATED,
    STATUS_VALIDATED,
    can_transition,
)

ARTIFACT_PLAN = "plan"
ARTIFACT_HOCON = "hocon"
ARTIFACT_QUALITY_CONTRACT = "quality_contract"

ARTIFACT_TYPES: tuple[str, ...] = (ARTIFACT_PLAN, ARTIFACT_HOCON, ARTIFACT_QUALITY_CONTRACT)


def sha256_of(text: str) -> str:
    """制品摘要：UTF-8 编码后 SHA256（FR-26 冻结口径）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(payload: Any) -> str:
    """规范化 JSON（键排序 + 紧凑分隔）：同内容恒得同摘要，跨进程可复现。"""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def artifact_digests(
    plan: Mapping[str, Any],
    hocon: str,
    quality_contract: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """计算三类制品摘要（plan / hocon / quality_contract）。

    `quality_contract` 缺省时取 `plan["quality_contract"]`——质量契约本身是 plan 的一部分，
    单独冻结是为了 FR-27 版本 Diff 能按类型比较。
    """
    contract = quality_contract
    if contract is None:
        candidate = plan.get("quality_contract")
        contract = candidate if isinstance(candidate, Mapping) else {}
    return {
        ARTIFACT_PLAN: sha256_of(canonical_json(plan)),
        ARTIFACT_HOCON: sha256_of(hocon),
        ARTIFACT_QUALITY_CONTRACT: sha256_of(canonical_json(contract)),
    }


async def freeze_version_artifacts(
    session: AsyncSession,
    version_id: uuid.UUID | str,
    plan: Mapping[str, Any],
    hocon: str,
    quality_contract: Mapping[str, Any] | None = None,
    *,
    expected_lock_version: int | None = None,
) -> str:
    """冻结候选制品：写 `pipeline_artifacts` 摘要 + 版本转 `VALIDATED`。

    版本级 `artifact_sha256` 取 **HOCON 摘要**——HOCON 是数据面实际执行物；
    plan / quality_contract 摘要作为独立制品行保留，供 FR-27 版本 Diff 使用。

    Args:
        session: 请求级数据库会话（调用方负责 commit）。
        version_id: 目标版本。
        plan: 候选 EtlPlan（只取摘要，正文不落业务库，FR-67）。
        hocon: 候选 HOCON（同上）。
        quality_contract: 质量契约；缺省取 `plan["quality_contract"]`。
        expected_lock_version: 期望的 `lock_version`（并发保护）。

    Returns:
        str: 版本级 artifact_sha256（64-hex）。

    Raises:
        VersionNotFound:      版本不存在。
        VersionStateConflict: 版本不是 GENERATED（须先生成候选），或已冻结且摘要不一致。
    """
    version = await session.get(PipelineVersion, uuid.UUID(str(version_id)))
    if version is None:
        raise VersionNotFound("Pipeline 版本不存在")

    digests = artifact_digests(plan, hocon, quality_contract)
    if version.status == STATUS_VALIDATED:
        if version.artifact_sha256 == digests[ARTIFACT_HOCON]:
            return version.artifact_sha256  # 幂等：同一内容重复冻结返回既有摘要
        raise VersionStateConflict("版本已冻结且摘要不一致：不可原地覆盖，请派生新版本（FR-26）")
    if not can_transition(version.status, STATUS_VALIDATED):
        raise VersionStateConflict(
            f"版本状态 {version.status} 不可冻结：须先产出候选并进入 {STATUS_GENERATED}"
        )

    session.add_all(
        [
            PipelineArtifact(
                pipeline_version_id=version.id, artifact_type=artifact_type, sha256=digest
            )
            for artifact_type, digest in digests.items()
        ]
    )
    await update_with_lock(
        session,
        version,
        expected_lock_version=expected_lock_version,
        status=STATUS_VALIDATED,
        artifact_sha256=digests[ARTIFACT_HOCON],
    )
    return digests[ARTIFACT_HOCON]


async def list_artifacts(
    session: AsyncSession, version_id: uuid.UUID
) -> list[PipelineArtifact]:
    """列出版本的制品摘要行（只有 `artifact_type` + `sha256`，无正文）。"""
    result = await session.execute(
        select(PipelineArtifact)
        .where(PipelineArtifact.pipeline_version_id == version_id)
        .order_by(PipelineArtifact.artifact_type)
    )
    return list(result.scalars().all())


async def artifact_digest_map(session: AsyncSession, version_id: uuid.UUID) -> dict[str, str]:
    """版本制品摘要映射 `{artifact_type: sha256}`（供版本 Diff 使用）。"""
    return {
        item.artifact_type: item.sha256 for item in await list_artifacts(session, version_id)
    }


async def require_frozen(
    session: AsyncSession, version_id: uuid.UUID, project_id: uuid.UUID
) -> tuple[PipelineVersion, dict[str, str]]:
    """加载版本并要求其已冻结制品（Prepare 前置，FR-31）。

    Raises:
        VersionNotFound:      版本不存在或跨项目。
        ArtifactNotFrozen:    版本尚无 `artifact_sha256`（未通过门禁冻结）。
    """
    version, _ = await load_version(session, version_id, project_id)
    if not version.artifact_sha256:
        raise ArtifactNotFrozen("版本尚未通过门禁冻结制品，不可创建准备单（FR-31）")
    return version, await artifact_digest_map(session, version.id)
