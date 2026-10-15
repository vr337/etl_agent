"""M-K 执行与数据面 — 表级指标采集（源/目标行数/字节，FR-39）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 5.2 章 execution_runs（source_rows / sink_rows /
source_bytes / sink_bytes / table_metrics）/ 第 6.1 章 API-12
覆盖 FR：FR-39（运行管理与监控：源/目标行数、字节、表级指标）
对应 AC：AC-06（回传指标可观测）、AC-07 前置（运行中心实时指标）

口径：控制面只存**聚合指标**（SPEC 5.3 Execution State 行：治理事实与聚合指标），
表级明细以 `table_metrics` JSON 承载 `{table, rows, bytes}` 列表——不含任何业务数据正文。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import ExecutionRun


@dataclass(frozen=True)
class TableMetric:
    """单表指标（源或目标；不含业务数据正文，仅聚合事实）。"""

    table: str
    rows: int
    bytes: int


@dataclass(frozen=True)
class ExecutionMetrics:
    """一次回传的聚合指标（None 表示该维度未观测到，不臆造 0）。"""

    source_rows: int | None = None
    sink_rows: int | None = None
    source_bytes: int | None = None
    sink_bytes: int | None = None
    tables: list[TableMetric] = field(default_factory=list)


async def apply_metrics(
    session: AsyncSession, run: ExecutionRun, metrics: ExecutionMetrics
) -> ExecutionRun:
    """把聚合指标写回运行并刷新 last_observed_at（FR-39：周期回传）。

    幂等语义：同一维度重复回传取**后值覆盖**（引擎侧指标为累计口径时由客户端
    先做差值，本模块只存事实）。
    """
    if metrics.source_rows is not None:
        run.source_rows = metrics.source_rows
    if metrics.sink_rows is not None:
        run.sink_rows = metrics.sink_rows
    if metrics.source_bytes is not None:
        run.source_bytes = metrics.source_bytes
    if metrics.sink_bytes is not None:
        run.sink_bytes = metrics.sink_bytes
    if metrics.tables:
        run.table_metrics = {
            "tables": [
                {"table": item.table, "rows": item.rows, "bytes": item.bytes}
                for item in metrics.tables
            ]
        }
    run.last_observed_at = datetime.now(UTC)
    await session.flush()
    return run


def metrics_from_engine_payload(payload: dict[str, Any]) -> ExecutionMetrics:
    """从引擎回传载荷构造聚合指标（未知字段忽略，缺失维度为 None）。

    期望形态（SeaTunnel 作业指标或等价结构）：
    `{"source_rows": 100, "sink_rows": 90, "source_bytes": 2048, "sink_bytes": 1024,
      "tables": [{"table": "orders", "rows": 90, "bytes": 1024}]}`
    """
    tables = [
        TableMetric(
            table=str(item.get("table", "")),
            rows=int(item.get("rows", 0) or 0),
            bytes=int(item.get("bytes", 0) or 0),
        )
        for item in _as_list(payload.get("tables"))
        if isinstance(item, dict)
    ]
    return ExecutionMetrics(
        source_rows=_as_int(payload.get("source_rows")),
        sink_rows=_as_int(payload.get("sink_rows")),
        source_bytes=_as_int(payload.get("source_bytes")),
        sink_bytes=_as_int(payload.get("sink_bytes")),
        tables=tables,
    )


def _as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
