"""M-D 元数据探查与数据分级 — API 请求 / 响应模型。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-04 / 第 3.2 章 M-D / 第 5.2 章 data_profiles
覆盖 FR：FR-12（表级只读 Profile）、FR-14（脱敏样本）、FR-15（6 级分级）
对应 AC：AC-02（响应含脱敏样本，绝不含样本明文）
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

# 端点角色（SPEC 5.2 data_profiles.endpoint_role）
ENDPOINT_ROLES = frozenset({"SOURCE", "SINK"})

# 采样上限（FR-12：1–100）
SAMPLE_LIMIT_MIN = 1
SAMPLE_LIMIT_MAX = 100

# Profile 过期时间默认 24h（SPEC 5.2 data_profiles.expires_at）
PROFILE_TTL_HOURS = 24


class ProfileCreateRequest(BaseModel):
    """Profile 探查请求（API-04）。

    sample_limit 缺省取 `ETL_AGENT_PROFILE_SAMPLE_LIMIT_DEFAULT`；
    越界由 service 层返回 BIZ_422_SAMPLE_LIMIT_OUT_OF_RANGE（而非静默截断）。
    """

    connection_id: uuid.UUID
    endpoint_role: str = Field(min_length=1, max_length=16)
    schema_name: str = Field(min_length=1, max_length=255)
    table_name: str = Field(min_length=1, max_length=255)
    sample_limit: int | None = None
    # 显式声明（优先级 1）与显式映射传播（优先级 3），见 classification.infer_level
    column_classifications: dict[str, str] | None = None
    column_classification_map: dict[str, str] | None = None


class ProfileColumn(BaseModel):
    """列级 Profile：类型 / 主键 / 近似统计 / 分级（FR-12/15）。"""

    name: str
    type: str
    nullable: bool
    is_primary_key: bool
    null_rate: float | None
    distinct_estimate: int | None
    classification: str
    effective_classification: str


class ProfileResponse(BaseModel):
    """Profile 响应（API-04）：`masked_samples` 已经过脱敏，绝不含明文（AC-02）。"""

    id: uuid.UUID
    connection_id: uuid.UUID
    endpoint_role: str
    schema_name: str
    table_name: str
    columns: list[ProfileColumn]
    masked_samples: list[dict[str, object]]
    approximate_row_count: int
    classification_report_sha256: str
    expires_at: datetime
    created_at: datetime


class ProfileSummaryView(BaseModel):
    """Profile 列表项（联调修复新增；前端 `ProfileView` 对齐）。

    与 `ProfileResponse` 的差异：列表**不回列明细与脱敏样本**——样本是逐表探查的重
    载荷，且前端列表只需要「探查是否完成 + 分级摘要」。样本经已有的
    `GET /projects/{id}/profiles` 之外的探查入口获取（本端点保持轻量）。
    """

    id: uuid.UUID
    connection_id: uuid.UUID
    schema_name: str
    table_name: str
    endpoint_role: str
    column_count: int
    primary_key: list[str] | None = None
    report_sha256: str
    classifications: dict[str, int] = Field(
        default_factory=dict, description="分级 → 列数（effective_classification 聚合）"
    )
    sample_status: str = Field(description="AVAILABLE / NONE / EXPIRED（依据 expires_at）")
    created_at: datetime
