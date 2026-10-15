"""M-L 受管回滚 — API-13 出入参模型（回滚申请 / 审批 / 执行状态）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-13 / 第 5.2 章 rollback_requests /
rollback_cleanup_requests / 第 6.4 章错误码
覆盖 FR：FR-48（回滚申请 / 审批 / 执行 / 制品清单）
对应 AC：AC-08（回滚走受管链路）、AC-02（响应零明文）

红线：出参**只回治理事实与制品引用**——不含凭据、不含业务数据、不含连接串。
制品清单只含表名与 `doris://` 引用（AC-02 / FR-48）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RollbackCreateRequest(BaseModel):
    """API-13 入参：对某次执行运行申请受管回滚。

    只声明「回滚到哪个运行的制品」与治理事实；数据面动作由 Harness 副作用协议执行，
    本请求**不**携带任何表数据或凭据。
    """

    reason: str = Field(min_length=1, max_length=1000, description="回滚原因")
    target_table: str | None = Field(default=None, description="正式表名（缺省由制品清单提供）")
    shadow_table: str | None = Field(default=None, description="影子表名（清理目标）")
    error_table: str | None = Field(default=None, description="错误表名（可选）")
    retention_days: int | None = Field(
        default=None, ge=1, le=7, description="保留期（默认 7 天，只收窄不放宽）"
    )
    capacity_warn_threshold: int | None = Field(
        default=None, ge=1, le=20, description="容量预警阈值（默认 20，只收窄不放宽）"
    )


class RollbackDecisionRequest(BaseModel):
    """回滚申请审批入参（治理层裁决，与 Harness 四眼审批分工不同）。"""

    decision: str = Field(description="APPROVE / REJECT")
    reason: str | None = Field(default=None, max_length=1000)


class RollbackCleanupRequestInput(BaseModel):
    """到期清理扫描入参（可指定保留期覆盖；实际执行仍走 Harness 协议）。"""

    retention_days: int | None = Field(default=None, ge=1, le=7)


class RollbackResponse(BaseModel):
    """回滚申请视图（API-13 出参；只含治理事实与制品引用）。"""

    id: uuid.UUID
    run_id: uuid.UUID
    status: str
    reason: str | None = None
    requested_by: uuid.UUID
    decided_by: uuid.UUID | None = None
    decision: str | None = None
    retention_days: int
    capacity_warn_threshold: int
    artifact_status: str | None = None
    artifact_manifest: dict[str, Any] | None = None
    executed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class RollbackCleanupView(BaseModel):
    """清理申请视图（只含引用，不含数据）。"""

    id: uuid.UUID
    rollback_request_id: uuid.UUID | None = None
    status: str
    reason: str | None = None
    requested_by: uuid.UUID
    executed_at: datetime | None = None
    created_at: datetime


class CleanupScanResponse(BaseModel):
    """到期清理扫描结果（供运维与运行中心展示）。"""

    evaluated: int
    created: int
    skipped: int
    retention_summary: str
    capacity_warning: bool
    details: list[dict[str, str]] = Field(default_factory=list)


__all__ = [
    "CleanupScanResponse",
    "RollbackCleanupRequestInput",
    "RollbackCleanupView",
    "RollbackCreateRequest",
    "RollbackDecisionRequest",
    "RollbackResponse",
]
