"""S7 可观测域共享夹具。

OTel 的全局 TracerProvider 一个进程只能设置一次、instrumentor 是进程级单例——
测试之间必须显式重置，否则后续用例会受前一个用例的全局状态影响
（典型症状：HTTPX 客户端埋点留下的活动 span 让「入站 trace_id 透传」断言失败）。
"""
from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

from etl_agent.observability.otel import shutdown_otel


def _uninstrument_all() -> None:
    """撤销四组件埋点（已未埋点 / 版本差异均静默跳过）。"""
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    for instrumentor in (
        FastAPIInstrumentor(),
        HTTPXClientInstrumentor(),
        SQLAlchemyInstrumentor(),
        CeleryInstrumentor(),
    ):
        if not getattr(instrumentor, "is_instrumented_by_opentelemetry", False):
            continue
        try:
            instrumentor.uninstrument()
        except Exception:
            logging.getLogger(__name__).debug("撤销埋点失败（忽略）", exc_info=True)


@pytest.fixture(autouse=True)
def _reset_otel_state() -> Iterator[None]:
    """每个用例前后重置 OTel 全局状态（幂等守卫 + 埋点单例的清理点）。"""
    shutdown_otel()
    _uninstrument_all()
    yield
    _uninstrument_all()
    shutdown_otel()
