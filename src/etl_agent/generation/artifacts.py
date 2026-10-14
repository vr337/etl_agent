"""M-F 生成引擎 — 不可变制品冻结（SHA256）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-F（FR-26 冻结部分）/ 第 5.2 章 pipeline_artifacts / 第 6.1 章 API-07
覆盖 FR：FR-26（制品 SHA256 冻结，版本只能派生不可原地覆盖）
对应 AC：AC-04（通过门禁后生成不可变版本摘要）

S5 范围（S5 提示词 11.9 API-07 说明）：**只做「通过门禁后冻结 SHA256」这一步**；
完整版本链状态机（DRAFT→…→APPROVED）与派生版本在 S6 完成。

双库分离口径（FR-67）：`pipeline_artifacts` 只存 `sha256`，**不存正文**——
候选 plan / HOCON 正文仅存在于 `etl_checkpoint` 库的 Checkpoint 中。
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.errors import NotFound
from etl_agent.infrastructure.models import Pipeline, PipelineArtifact, PipelineVersion

ARTIFACT_PLAN = "plan"
ARTIFACT_HOCON = "hocon"

# 版本冻结后的状态（完整链路状态机 S6 接管）
STATUS_VALIDATED = "VALIDATED"


def sha256_of(text: str) -> str:
    """制品摘要：UTF-8 编码后 SHA256（FR-26 冻结口径）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def freeze_version_artifacts(
    session: AsyncSession,
    version_id: str | uuid.UUID,
    plan: dict[str, Any],
    hocon: str,
) -> str:
    """冻结候选制品：写 `pipeline_artifacts` 摘要 + 回写版本 `artifact_sha256`。

    版本摘要取 HOCON 的 SHA256——HOCON 是数据面实际执行物；
    plan 摘要作为独立制品行保留，供 S6 版本 Diff 使用。

    Args:
        session: 请求级数据库会话（调用方负责 commit）。
        version_id: 目标 PipelineVersion。
        plan: 候选 EtlPlan（只取摘要，正文不落业务库，FR-67）。
        hocon: 候选 HOCON（同上）。

    Returns:
        str: 版本级 artifact_sha256（64-hex）。

    Raises:
        NotFound: 版本不存在。
    """
    version = await session.get(PipelineVersion, uuid.UUID(str(version_id)))
    if version is None:
        raise NotFound("Pipeline 版本不存在")

    import json

    plan_sha = sha256_of(json.dumps(plan, sort_keys=True, ensure_ascii=False))
    hocon_sha = sha256_of(hocon)
    session.add_all(
        [
            PipelineArtifact(
                pipeline_version_id=version.id, artifact_type=ARTIFACT_PLAN, sha256=plan_sha
            ),
            PipelineArtifact(
                pipeline_version_id=version.id, artifact_type=ARTIFACT_HOCON, sha256=hocon_sha
            ),
        ]
    )
    version.artifact_sha256 = hocon_sha
    version.status = STATUS_VALIDATED
    await session.flush()
    return hocon_sha


async def load_version_with_project(
    session: AsyncSession, version_id: uuid.UUID
) -> tuple[PipelineVersion, uuid.UUID]:
    """加载版本与其所属项目（API-07 无 project_id 路径参数，需回溯项目做鉴权）。

    Raises:
        NotFound: 版本不存在（BIZ_404_NOT_FOUND，SPEC 6.4 通用码，复用不新增）。
    """
    result = await session.execute(
        select(PipelineVersion, Pipeline.project_id)
        .join(Pipeline, PipelineVersion.pipeline_id == Pipeline.id)
        .where(PipelineVersion.id == version_id)
    )
    row = result.one_or_none()
    if row is None:
        raise NotFound("Pipeline 版本不存在")
    return row[0], row[1]
