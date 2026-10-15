"""结构化日志用例（T-20.3 / FR-60）。

覆盖：JSON 单行输出、trace_id / span_id 字段、异常栈进日志（不进响应）、
handler 装配幂等、自定义字段透传。
"""
from __future__ import annotations

import json
import logging

from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from etl_agent.observability.logging import (
    JsonFormatter,
    configure_logging,
    current_span_id,
    current_trace_id,
)
from etl_agent.observability.otel import build_tracer_provider
from tests.helpers import make_settings

ENABLED = {
    "otel_enabled": True,
    "otel_exporter_otlp_endpoint": "http://otel-collector:4317",
}


def _record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="etl_agent.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_produces_single_line_json() -> None:
    """单行 JSON，含固定字段（timestamp / level / logger / message）。"""
    payload = json.loads(JsonFormatter().format(_record("hello")))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "etl_agent.test"
    assert payload["message"] == "hello"
    assert "T" in payload["timestamp"]  # ISO8601
    assert payload["trace_id"] is None  # 无活动 span
    assert payload["span_id"] is None


def test_json_formatter_includes_trace_id() -> None:
    """有活动 span 时 trace_id / span_id 与 span 上下文一致（日志可关联链路）。"""
    provider = build_tracer_provider(make_settings(**ENABLED), exporter=InMemorySpanExporter())
    assert provider is not None
    span = provider.get_tracer("test").start_span("unit")

    with trace.use_span(span, end_on_exit=True):
        expected_trace = format(span.get_span_context().trace_id, "032x")
        expected_span = format(span.get_span_context().span_id, "016x")
        assert current_trace_id() == expected_trace
        assert current_span_id() == expected_span
        payload = json.loads(JsonFormatter().format(_record("with-span")))

    assert payload["trace_id"] == expected_trace
    assert payload["span_id"] == expected_span


def test_json_formatter_passes_extra_fields() -> None:
    """自定义字段透传（如 run_id / project_id，便于按资源检索）。"""
    payload = json.loads(
        JsonFormatter().format(_record("run", run_id="abc", attempt=2))
    )
    assert payload["run_id"] == "abc"
    assert payload["attempt"] == 2


def test_json_formatter_includes_exception_stack() -> None:
    """异常栈只进日志（响应体只回 code/message/trace_id，AC-02）。"""
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="etl_agent.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError" in payload["exception"]
    assert "boom" in payload["exception"]


def test_configure_logging_is_idempotent() -> None:
    """重复装配不叠加 JSON handler（避免同一行日志输出多次）。"""
    root = logging.getLogger()
    original = list(root.handlers)
    try:
        configure_logging(force=True)
        configure_logging()
        configure_logging()
        json_handlers = [
            handler
            for handler in root.handlers
            if isinstance(getattr(handler, "formatter", None), JsonFormatter)
        ]
        assert len(json_handlers) == 1
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in original:
            root.addHandler(handler)


def test_configure_logging_sets_level() -> None:
    """级别可配置（生产 INFO / 排查 DEBUG）。"""
    root = logging.getLogger()
    original_level = root.level
    original = list(root.handlers)
    try:
        configure_logging(level="debug", force=True)
        assert root.level == logging.DEBUG
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in original:
            root.addHandler(handler)
        root.setLevel(original_level)
