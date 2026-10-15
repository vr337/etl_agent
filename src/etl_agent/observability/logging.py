"""M-O 可观测性 — 结构化日志（JSON + trace_id，FR-60）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-O / 第 8 章「可观测」/ 第 6.4 章错误码（trace_id 贯通）
覆盖 FR：FR-60（结构化日志 + 错误响应附带 trace_id）
对应 AC：AC-02（日志正文零凭据）、AC-11（生产日志可被采集）

形态：每条日志一行 JSON（`timestamp` / `level` / `logger` / `message` / `trace_id` /
`span_id` / 异常栈），便于日志系统按字段检索与关联链路。

红线：本模块**不做任何字段脱敏兜底**——脱敏的责任在写日志的调用方
（AC-02：凭据明文不落日志是产生端的约束，不在输出端做「看起来像密码就删掉」的猜测）。
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

# JSON 日志固定字段（其余 record 属性按需附加）
_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


def current_trace_id() -> str | None:
    """当前活动 span 的 trace_id（32 位 hex）；无活动 span 返回 None。

    错误响应与日志共用本函数：同一个请求 / 任务的 trace_id 必须一致，
    否则「日志里搜得到、响应里对不上」等于没有链路。
    """
    span = trace.get_current_span()
    context = span.get_span_context() if span is not None else None
    if context is None or not context.is_valid:
        return None
    return format(context.trace_id, "032x")


def current_span_id() -> str | None:
    """当前活动 span 的 span_id（16 位 hex）；无活动 span 返回 None。"""
    span = trace.get_current_span()
    context = span.get_span_context() if span is not None else None
    if context is None or not context.is_valid:
        return None
    return format(context.span_id, "016x")


class JsonFormatter(logging.Formatter):
    """单行 JSON 日志格式器（含 trace_id / span_id 关联字段）。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": current_trace_id(),
            "span_id": current_span_id(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", *, force: bool = False) -> None:
    """把根日志器切到 JSON 结构化输出（幂等；重复调用不叠加 handler）。

    Args:
        level: 根日志器级别。
        force: 为 True 时清空既有 handler 后重建（测试用；生产不传，避免吞掉
            其他库预置的 handler）。
    """
    root = logging.getLogger()
    if force:
        for handler in list(root.handlers):
            root.removeHandler(handler)
    already = any(
        isinstance(getattr(handler, "formatter", None), JsonFormatter)
        for handler in root.handlers
    )
    if not already:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    root.setLevel(level.upper())


__all__ = [
    "JsonFormatter",
    "configure_logging",
    "current_span_id",
    "current_trace_id",
]
