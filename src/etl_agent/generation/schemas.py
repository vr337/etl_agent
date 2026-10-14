"""M-F 生成引擎 — API 请求 / 响应模型。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-05/06/07 / 第 6.2 章接口契约 / 第 5.2 章 agent_runs
覆盖 FR：FR-16（创建运行）、FR-17（澄清回答）、FR-26（版本冻结摘要）、FR-67（响应不含 Prompt / 候选正文）
对应 AC：AC-03（中断与恢复）、AC-02（响应零明文）

响应模型**有意缺席**的字段（FR-67 / AC-02 红线）：
`requirement`、`etl_plan`、`hocon`、任何候选正文与凭据——业务库不存、响应不回。
`pending_question` 只承载静态澄清文案，不含用户输入。
"""
from __future__ import annotations

import uuid

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


class PipelineVersionFreezeResponse(BaseModel):
    """API-07 冻结响应（S5 只做 SHA256 冻结，完整版本链留 S6）。"""

    id: uuid.UUID
    artifact_sha256: str
    status: str
