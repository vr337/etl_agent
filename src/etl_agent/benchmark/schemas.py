"""M-M 评测与安全进化 — API-15 出入参模型（运行 Benchmark / 查询运行）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-15 / 第 6.2 章接口契约 /
第 5.2 章 benchmark_runs
覆盖 FR：FR-50（运行版本化 Benchmark 套件）
对应 AC：AC-09（六项门禁）、AC-02（响应零明文）

红线：出参只含 `suite_id` / `manifest_*` / 状态 / 六项门禁实际值 / `report_sha256`；
**不回传报告正文与场景明细正文**（只给摘要与计数，AC-02 同口径）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BenchmarkRunRequest(BaseModel):
    """API-15 入参（SPEC 6.2）。"""

    suite_id: str = Field(default="harness-v2", max_length=128, description="套件标识")
    manifest_version: str = Field(default="v1", max_length=64, description="冻结版本")
    layers: list[str] = Field(
        default_factory=lambda: ["L0", "L1", "L2"], description="要跑的层级子集"
    )
    project_id: uuid.UUID | None = Field(
        default=None, description="归属项目（缺省用路径参数或服务默认）"
    )


class GateOutcomeView(BaseModel):
    """单项门禁判定视图。"""

    key: str
    actual: float
    operator: str
    threshold: float
    passed: bool


class LayerSummaryView(BaseModel):
    """单层结果摘要（只给计数，不给场景明细正文）。"""

    layer: str
    total: int
    passed: int
    blocked: int


class BenchmarkRunResponse(BaseModel):
    """API-15 出参（SPEC 6.2：run_id / status / metrics / report_sha256）。"""

    run_id: uuid.UUID
    suite_id: str
    manifest_version: str
    manifest_sha256: str
    status: str
    metrics: dict[str, float]
    gates: list[GateOutcomeView]
    failures: list[str] = Field(default_factory=list)
    layers: list[LayerSummaryView] = Field(default_factory=list)
    report_sha256: str
    trace_id: str | None = None


class BenchmarkRunListView(BaseModel):
    """运行列表（轻量视图）。"""

    runs: list[BenchmarkRunResponse]
    total: int


class BenchmarkRunDetailView(BaseModel):
    """运行详情（含创建时间；用于查询端点）。"""

    run_id: uuid.UUID
    suite_id: str
    manifest_version: str
    manifest_sha256: str
    status: str
    metrics: dict[str, Any]
    report_sha256: str | None = None
    created_at: datetime


__all__ = [
    "BenchmarkRunDetailView",
    "BenchmarkRunListView",
    "BenchmarkRunRequest",
    "BenchmarkRunResponse",
    "GateOutcomeView",
    "LayerSummaryView",
]
