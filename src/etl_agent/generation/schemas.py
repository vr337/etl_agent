"""M-F 生成引擎 — API 请求 / 响应模型。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-05/06 / 第 6.2 章接口契约 / 第 5.2 章 agent_runs
覆盖 FR：FR-16（创建运行）、FR-17（澄清回答）、FR-67（响应不含 Prompt / 候选正文）
对应 AC：AC-03（中断与恢复）、AC-02（响应零明文）

S6 变更：API-07 冻结响应模型随端点迁至 `versions/schemas.py`（版本域归属 M-H）。

响应模型**有意缺席**的字段（FR-67 / AC-02 红线）：
`requirement`、`etl_plan`、`hocon`、任何候选正文与凭据——业务库不存、响应不回。
`pending_question` 只承载静态澄清文案，不含用户输入。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AgentRunCreateRequest(BaseModel):
    """API-05 创建生成运行（FR-16）。

    `source_profile_ids` / `sink_profile_ids` 允许为空——此时状态机进入
    `WAITING_INPUT` 并挂起澄清问题（AC-03），而非参数校验失败。
    """

    requirement: str = Field(default="", max_length=8000)
    source_profile_ids: list[uuid.UUID] = Field(default_factory=list)
    sink_profile_ids: list[uuid.UUID] = Field(default_factory=list)
    file_asset_ids: list[uuid.UUID] = Field(default_factory=list)
    # 缺省时由服务自动创建 Pipeline + 首个版本（S5 不做版本链管理，S6 接管）
    pipeline_version_id: uuid.UUID | None = None


class AgentRunMessageRequest(BaseModel):
    """API-06 澄清回答（FR-17）。"""

    answer: str = Field(min_length=1, max_length=8000)


class AgentRunResponse(BaseModel):
    """运行响应（只含状态 + 挂起问题 + 非敏感遥测，FR-67）。"""

    id: uuid.UUID
    thread_id: str
    status: str
    pending_question: str | None = None
    generation_backend: str | None = None
    generation_model: str | None = None
    generation_candidate_count: int | None = None
    generation_repair_count: int | None = None
    generation_duration_ms: int | None = None


class AgentRunListView(BaseModel):
    """运行列表项（联调修复新增；前端 `AgentRunView` 对齐）。

    `pipeline_id` 经 `pipeline_version_id → PipelineVersion.pipeline_id` 派生（agent_runs
    无 pipeline_id 列）。遥测字段与 `AgentRunResponse` 同源，**不含 Prompt / 候选正文**
    （FR-67 红线：业务库禁存这两类字段）。
    """

    id: uuid.UUID
    thread_id: str
    pipeline_id: uuid.UUID
    status: str
    repair_count: int = 0
    clarify_question: str | None = None
    created_at: datetime | None = None
