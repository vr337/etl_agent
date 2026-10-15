"""M-M 评测与安全进化 — 安全进化域 API 出入参模型（提案 / 受控发布）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 5.2 章 safety_improvement_proposals /
safety_releases
覆盖 FR：FR-51（安全改进提案）、FR-52（受控发布）
对应 AC：AC-01（仅 SECURITY_APPROVER 裁决）、AC-02（观察样本脱敏聚合）

红线：出参只含治理事实——**不回传提案 payload 正文**（候选内容只用于离线审查，
入 API 响应会把未审内容扩散出去）；观察样本只回计数与比率，不含任何明细。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProposalCreateRequest(BaseModel):
    """创建安全改进提案（类型限定 POLICY/PROMPT/BENCHMARK）。"""

    type: str = Field(max_length=32, description="POLICY / PROMPT / BENCHMARK")
    title: str = Field(min_length=1, max_length=255)
    summary: str | None = Field(default=None, max_length=2000)
    payload: dict[str, Any] | None = Field(
        default=None, description="声明式候选（非可执行代码）"
    )
    benchmark_run_id: uuid.UUID | None = None


class ProposalDecisionRequest(BaseModel):
    """提案裁决（仅 SECURITY_APPROVER）。"""

    decision: str = Field(description="APPROVE / REJECT")
    reason: str | None = Field(default=None, max_length=2000)


class ProposalView(BaseModel):
    """提案视图（不含 payload 正文）。"""

    id: uuid.UUID
    project_id: uuid.UUID
    benchmark_run_id: uuid.UUID | None = None
    type: str
    status: str
    title: str
    summary: str | None = None
    created_by: uuid.UUID
    decided_by: uuid.UUID | None = None
    decision: str | None = None
    decision_reason: str | None = None
    decided_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ProposalListView(BaseModel):
    proposals: list[ProposalView]
    total: int


class ShadowStartRequest(BaseModel):
    """启动影子验证（清单摘要必填，用于签名绑定）。"""

    manifest_sha256: str = Field(min_length=64, max_length=64, description="发布清单 SHA256")


class CanaryRequest(BaseModel):
    """进入小流量（百分比必须 1%–10%）。"""

    canary_percent: int = Field(description="小流量百分比（1%–10%，SPEC FR-52）")
    manifest_sha256: str = Field(min_length=64, max_length=64)
    observation: ObservationView | None = None


class PromotionRequest(BaseModel):
    """晋升授权（附脱敏聚合观察样本）。"""

    manifest_sha256: str = Field(min_length=64, max_length=64)
    observation: ObservationView


class RollbackRequest(BaseModel):
    """受控回滚。"""

    manifest_sha256: str = Field(min_length=64, max_length=64)
    reason: str | None = Field(default=None, max_length=2000)


class ObservationView(BaseModel):
    """观察样本（**脱敏聚合事实**：只有计数与比率，无任何明细）。"""

    sample_size: int = Field(ge=0)
    anomaly_count: int = Field(ge=0)


class ReleaseView(BaseModel):
    """受控发布视图（签名以摘要形式回传，不回传 signature 本体之外的敏感信息）。"""

    id: uuid.UUID
    proposal_id: uuid.UUID
    stage: str
    canary_percent: int | None = None
    manifest_sha256: str
    signature_key_id: str | None = None
    created_at: datetime
    updated_at: datetime


__all__ = [
    "CanaryRequest",
    "ObservationView",
    "PromotionRequest",
    "ProposalCreateRequest",
    "ProposalDecisionRequest",
    "ProposalListView",
    "ProposalView",
    "ReleaseView",
    "RollbackRequest",
    "ShadowStartRequest",
]
