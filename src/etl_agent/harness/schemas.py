"""M-J Harness 安全执行内核 — API 请求 / 响应模型。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-08/09/10 / 第 6.2 章接口契约 / 第 6.3 章 OpenAPI 片段
覆盖 FR：FR-31（准备单）、FR-32（审批决策）、FR-33（Commit 与 Capability）
对应 AC：AC-01（四眼审批）、AC-05（prepare→approve→commit）

响应只回治理事实：不回显 rule_evidence 全量证据以外的东西，绝不回令牌本体
（令牌经 Worker 通道转交；API 只回 `token_id` 与 claims）。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

ALLOWED_ENVIRONMENTS = ("dev", "staging", "prod")
ALLOWED_DECISIONS = ("APPROVE", "REJECT")
APPROVER_ROLES = ("DATA_APPROVER", "SECURITY_APPROVER")


class PreparationCreateRequest(BaseModel):
    """API-08 创建准备单（FR-31）。"""

    pipeline_version_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=255)
    environment: str = Field(default="dev", pattern="^(dev|staging|prod)$")
    tool_name: str = Field(default="pipeline.execute", max_length=128)
    tool_version: str = Field(default="1.0.0", max_length=64)
    budget: dict[str, int] | None = None
    target_schema_aligned: bool | None = None
    classifications: list[str] = Field(default_factory=list)
    side_effect_type: str | None = Field(default=None, max_length=64)
    resource_ids: list[uuid.UUID] = Field(default_factory=list)


class PreparationResponse(BaseModel):
    """准备单响应（API-08 出参，SPEC 6.2）。"""

    id: uuid.UUID
    status: str
    risk_level: str
    artifact_sha256: str | None = None
    input_fingerprint: str | None = None
    resource_fingerprint: str | None = None
    required_roles: list[str] = Field(default_factory=list)
    required_approvals: int = 0
    environment: str | None = None
    tool_name: str | None = None
    tool_version: str | None = None
    expires_at: datetime | None = None
    outcome: str = "ALLOW"


class DecisionRequest(BaseModel):
    """API-09 审批决策（FR-32）。"""

    role: str = Field(pattern="^(DATA_APPROVER|SECURITY_APPROVER)$")
    decision: str = Field(pattern="^(APPROVE|REJECT)$")
    reason: str | None = Field(default=None, max_length=2000)
    evidence_sha256: str | None = Field(default=None, min_length=64, max_length=64)


class DecisionResponse(BaseModel):
    """API-09 出参。"""

    preparation_id: uuid.UUID
    role: str
    decision: str
    decided_by: uuid.UUID
    status: str
    approvals_received: int
    approvals_required: int


class CommitRequest(BaseModel):
    """API-10 Commit（SPEC 6.3：operator_id 或由身份头解析）。"""

    operator_id: uuid.UUID | None = None


class CommitResponse(BaseModel):
    """API-10 出参：只回 token_id 与 claims，**不返回令牌本体**（FR-33）。"""

    token_id: uuid.UUID
    preparation_id: uuid.UUID
    claims: dict[str, object]
    expires_at: datetime
    run_id: uuid.UUID
    outbox_id: uuid.UUID


class ApprovalSlotView(BaseModel):
    """审批槽位事实（前端 ApprovalCard 消费；字段取自 `harness_approval_decisions`）。"""

    role: str
    decided_by: uuid.UUID | None = None
    decision: str | None = None
    reason: str | None = None
    evidence_sha256: str | None = None
    decided_at: datetime | None = None


class PreparationListView(BaseModel):
    """API-08 列表出参：项目下准备单（前端审批台消费；展示口径）。

    与 `PreparationResponse`（创建出参）的差异：列表需带 `pipeline_version_id`、
    `decision`（PDP 判决）与审批槽位事实，供前端「待四眼审批」卡与「申请人禁自批」
    判定；创建出参只回新建事实。两模型指向同一行，无新增事实。
    """

    id: uuid.UUID
    pipeline_version_id: uuid.UUID
    decision: str = Field(description="PDP 判决（ALLOW / DENY / REQUIRE_APPROVAL / DEFER）")
    risk_level: str
    status: str
    required_roles: list[str] = Field(default_factory=list)
    required_approvals: int = 0
    approvals: list[ApprovalSlotView] = Field(default_factory=list)
    impact_report: dict[str, object] | None = None
    rollback_plan: str | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None
