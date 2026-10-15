"""M-K 执行与数据面 — API 出入参模型（API-11 触发执行 / API-12 查询执行 / API-14 质量报告）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-11/12/14 / 第 5.2 章 execution_runs、
execution_events、quality_reports / 第 6.4 章错误码
覆盖 FR：FR-38（触发受管执行）、FR-39（状态 / 事件流 / 行数字节）、FR-44（质量报告）、FR-45（监督快照）
对应 AC：AC-06（执行可触发可查询）、AC-02（响应零明文）

红线：出参**只回治理事实与聚合指标**——不含 HOCON 正文、不含凭据、不含业务数据。
监督快照（S8 落 `runtime_supervision_snapshots`）在 S7 恒为 None：本阶段不做监督。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ExecutionTriggerRequest(BaseModel):
    """API-11 入参：对已 Commit 的准备单触发受管执行。

    `pipeline_version_id` 为可选一致性校验项（准备单已登记版本，此处再声明一次
    即等于要求调用方与登记事实对齐；不一致直接 409，防串版本触发）。
    """

    preparation_id: uuid.UUID = Field(description="已 Commit 的 Harness 准备单")
    trigger: str = Field(default="MANUAL", description="MANUAL / SCHEDULED")
    pipeline_version_id: uuid.UUID | None = Field(
        default=None, description="可选：与准备单登记版本做一致性校验"
    )


class ExecutionEventView(BaseModel):
    """执行事件流条目（FR-39：level / stage / event_type）。"""

    level: str
    stage: str | None = None
    event_type: str
    message: str
    occurred_at: datetime


class ExecutionRunView(BaseModel):
    """执行运行视图（API-11/12 共用；S7 无监督快照，字段保留待 S8 填充）。"""

    id: uuid.UUID
    pipeline_version_id: uuid.UUID
    harness_preparation_id: uuid.UUID
    status: str
    trigger: str
    seatunnel_job_id: str | None = None
    engine_status: str | None = None
    scheduled_for: datetime | None = None
    source_rows: int | None = None
    sink_rows: int | None = None
    source_bytes: int | None = None
    sink_bytes: int | None = None
    table_metrics: dict[str, Any] | None = None
    retry_of_run_id: uuid.UUID | None = None
    attempt_number: int = 1
    failure_category: str | None = None
    failure_stage: str | None = None
    retryable: bool | None = None
    diagnosis: str | None = None
    submitted_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_observed_at: datetime | None = None


class ExecutionDetailResponse(ExecutionRunView):
    """API-12 出参：运行 + 事件流 + 监督快照（S7 恒 None）。"""

    events: list[ExecutionEventView] = Field(default_factory=list)
    supervision_snapshot: dict[str, Any] | None = None


class ExecutionTriggerResponse(BaseModel):
    """API-11 出参：触发结果（是否已即时派发 + 当前运行事实）。"""

    run: ExecutionRunView
    dispatched: bool = Field(
        description="是否已即时派发扫描任务；False 表示由 Beat 周期（默认 2s）兜底领取"
    )


class QualityReportView(BaseModel):
    """API-14 出参：质量契约与分流报告（只回聚合，原始值留在错误表，PRD 10.5）。"""

    run_id: uuid.UUID
    status: str
    total_rows: int | None = None
    passed_rows: int | None = None
    rejected_rows: int | None = None
    reject_rate: float | None = None
    valid_sink: str | None = None
    error_sink: str | None = None
    rules: list[dict[str, Any]] = Field(default_factory=list)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    generated_at: datetime | None = None


__all__ = [
    "ExecutionDetailResponse",
    "ExecutionEventView",
    "ExecutionRunView",
    "ExecutionTriggerRequest",
    "ExecutionTriggerResponse",
    "QualityReportView",
]
