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

    期望形态一（mock / 演示态，SeaTunnel 作业指标或等价结构）：
    `{"source_rows": 100, "sink_rows": 90, "source_bytes": 2048, "sink_bytes": 1024,
      "tables": [{"table": "orders", "rows": 90, "bytes": 1024}]}`

    期望形态二（真实引擎 REST `/job-info` 的 metrics，T-ENGINE-02 实测）：
    `{"TableSourceReceivedCount": {"Source[0].orders": "1002"},
      "TableSinkWriteCount": {"Sink[0].orders": "1000"},
      "SinkCommittedCount": "1000", ...}`
    ——值为字符串、行数指标为「顶点→计数」嵌套 dict；本函数对两种形态兼容
    （mock 键优先，真实键兜底），bytes / tables 在真实形态下留 None / []（TODO）。

    null-safe：任何缺失 / 畸形（非数字字符串、空 dict）都不抛，对应维度为 None。
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
    source_rows = _as_int(payload.get("source_rows"))
    if source_rows is None:
        # 真实引擎：TableSourceReceivedCount 为 {顶点: "计数"} 嵌套，取合计
        source_rows = _as_nested_sum(payload.get("TableSourceReceivedCount"))
    sink_rows = _as_int(payload.get("sink_rows"))
    if sink_rows is None:
        # 优先按写入计数，退化用提交计数（真实引擎两者都有时写入更贴近落库）
        sink_rows = _as_nested_sum(payload.get("TableSinkWriteCount"))
    if sink_rows is None:
        sink_rows = _as_nested_sum(payload.get("SinkCommittedCount"))
    return ExecutionMetrics(
        source_rows=source_rows,
        sink_rows=sink_rows,
        source_bytes=_as_int(payload.get("source_bytes")),
        sink_bytes=_as_int(payload.get("sink_bytes")),
        tables=tables,
    )


def _as_nested_sum(value: object) -> int | None:
    """真实引擎行数指标聚合：嵌套 dict 取 value 合计，扁平标量取原值（null-safe）。

    Args:
        value: `{"Source[0].orders": "1002"}` / `"1002"` / 1002 / None / 畸形值。

    Returns:
        int | None：取不到有效数字时 None（由调用方降级，不抛异常）。
    """
    if isinstance(value, dict):
        total = 0
        matched = False
        for item in value.values():
            number = _coerce_int(item)
            if number is not None:
                total += number
                matched = True
        return total if matched else None
    return _coerce_int(value)


def _coerce_int(value: object) -> int | None:
    """宽松整数转换（bool 不算 int；数字字符串去空白；其余 None）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            return int(text)
    return None


def _as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
