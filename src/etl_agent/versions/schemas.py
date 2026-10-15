"""M-H 不可变版本与设计审批 — API 请求 / 响应模型。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-07 / 第 6.2 章接口契约 / 第 5.2 章 pipeline_versions
覆盖 FR：FR-26（冻结与派生）、FR-27（版本 Diff）、FR-28（设计审批）
对应 AC：AC-04（不可变版本摘要）、AC-17（版本 Diff）

响应只回**摘要与状态**：候选 plan / HOCON 正文留在 `etl_checkpoint`（FR-67 / S5 吸收 2），
Diff 的正文差异由请求显式触发且只回差异结构、不回正文全文。
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class VersionArtifact(BaseModel):
    """制品摘要行（只有类型 + SHA256，无正文）。"""

    artifact_type: str
    sha256: str


class PipelineVersionResponse(BaseModel):
    """版本状态与制品摘要。"""

    id: uuid.UUID
    pipeline_id: uuid.UUID
    version_number: int
    status: str
    artifact_sha256: str | None = None
    parent_version_id: uuid.UUID | None = None
    lock_version: int
    artifacts: list[VersionArtifact] = Field(default_factory=list)


class FreezeRequest(BaseModel):
    """API-07 冻结请求（可省略正文：省略时从 Checkpoint 读回该版本候选正文）。

    `expected_lock_version` 用于并发保护：与库内不一致 → 409。
    """

    plan: dict[str, object] | None = None
    hocon: str | None = None
    quality_contract: dict[str, object] | None = None
    expected_lock_version: int | None = None


class FreezeResponse(BaseModel):
    """API-07 冻结响应（版本级摘要 + 制品明细）。"""

    id: uuid.UUID
    artifact_sha256: str
    status: str
    artifacts: list[VersionArtifact] = Field(default_factory=list)


class DeriveResponse(BaseModel):
    """派生新版本响应（FR-26：修改必须派生，禁原地覆盖）。"""

    id: uuid.UUID
    pipeline_id: uuid.UUID
    version_number: int
    status: str
    parent_version_id: uuid.UUID | None = None


class ApprovalRequestResponse(BaseModel):
    """设计审批申请（FR-28）：绑定 artifact_sha256。"""

    id: uuid.UUID
    pipeline_version_id: uuid.UUID
    artifact_sha256: str
    status: str
    decision: str | None = None
    requested_by: uuid.UUID


class ApprovalDecisionRequest(BaseModel):
    """设计审批决策（API：APPROVE / REJECT + 理由留痕）。"""

    decision: str = Field(pattern="^(APPROVE|REJECT)$")
    reason: str | None = Field(default=None, max_length=2000)


class ApprovalDecisionResponse(BaseModel):
    """设计审批决策响应。"""

    id: uuid.UUID
    pipeline_version_id: uuid.UUID
    status: str
    decision: str | None = None
    decided_by: uuid.UUID | None = None
    version_status: str


class VersionDiffResponse(BaseModel):
    """版本 Diff 响应（FR-27）：摘要级差异恒可用；正文级差异视 Checkpoint 可得性。"""

    version_id: uuid.UUID
    against_version_id: uuid.UUID
    artifact_digests: dict[str, dict[str, object]]
    content_available: bool
    schema_diff: dict[str, object] | None = None
    quality_contract_diff: dict[str, object] | None = None
    hocon_diff: dict[str, object] | None = None
