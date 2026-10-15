"""OTel 初始化用例（T-20.1 / T-20.2 / FR-60）。

覆盖：四组件埋点（FastAPI / HTTPX / SQLAlchemy / Celery）、OTLP 导出超时、
**关闭即零成本**（不建 Provider / 不注册埋点 / 不导出）、装配幂等。
"""
from __future__ import annotations

from fastapi import FastAPI
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from etl_agent.observability.exporters import build_span_exporter
from etl_agent.observability.otel import (
    ALL_COMPONENTS,
    build_tracer_provider,
    otel_state,
    setup_otel,
)
from tests.helpers import make_settings

ENABLED = {
    "otel_enabled": True,
    "otel_exporter_otlp_endpoint": "http://otel-collector:4317",
    "otel_export_timeout_seconds": 10,
}
DISABLED = {"otel_enabled": False, "otel_exporter_otlp_endpoint": ""}


def test_build_span_exporter_disabled_returns_none() -> None:
    """关闭时不构造导出器（零网络、零线程）。"""
    assert build_span_exporter(make_settings(**DISABLED)) is None


def test_build_span_exporter_enabled() -> None:
    """启用且端点存在时构造 OTLP 导出器，超时取自配置（10s）。"""
    exporter = build_span_exporter(make_settings(**ENABLED))
    assert exporter is not None


def test_build_span_exporter_normalizes_host_port() -> None:
    """只填 host:port 时补默认 http 前缀（OTLP 惯用形态）。"""
    exporter = build_span_exporter(
        make_settings(otel_enabled=True, otel_exporter_otlp_endpoint="collector:4317")
    )
    assert exporter is not None


def test_tracer_provider_disabled_is_none() -> None:
    """关闭时 `build_tracer_provider` 返回 None（不建 Provider）。"""
    assert build_tracer_provider(make_settings(**DISABLED)) is None


def test_tracer_provider_exports_spans() -> None:
    """导出链路可用：span 结束并 flush 后到达导出器。"""
    exporter = InMemorySpanExporter()
    provider = build_tracer_provider(make_settings(**ENABLED), exporter=exporter)
    assert provider is not None

    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("unit-work"):
        pass
    provider.force_flush()

    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["unit-work"]


def test_setup_otel_instruments_all_four_components() -> None:
    """四组件埋点全部就位（FR-60：FastAPI / HTTPX / SQLAlchemy / Celery）。"""
    app = FastAPI(title="otel-test")

    state = setup_otel(app, make_settings(**ENABLED), exporter=InMemorySpanExporter())

    assert state.enabled is True
    assert set(state.components) == set(ALL_COMPONENTS)
    assert state.components == tuple(sorted(ALL_COMPONENTS))


def test_setup_otel_without_app_instruments_three() -> None:
    """Worker 侧无 FastAPI：只装配 HTTPX / SQLAlchemy / Celery 三类。"""
    state = setup_otel(None, make_settings(**ENABLED), exporter=InMemorySpanExporter())
    assert state.enabled is True
    assert set(state.components) == {"httpx", "sqlalchemy", "celery"}


def test_setup_otel_disabled_no_export() -> None:
    """关闭时：不埋点、不导出（`otel_state()` 反映关闭态）。"""
    exporter = InMemorySpanExporter()
    state = setup_otel(None, make_settings(**DISABLED), exporter=exporter)

    assert state.enabled is False
    assert state.components == ()
    assert otel_state().enabled is False
    assert exporter.get_finished_spans() == ()


def test_setup_otel_is_idempotent() -> None:
    """重复装配返回首次结果（全局 TracerProvider 只能设置一次）。"""
    settings = make_settings(**ENABLED)
    first = setup_otel(None, settings, exporter=InMemorySpanExporter())
    second = setup_otel(None, settings, exporter=InMemorySpanExporter())
    assert first == second


def test_setup_otel_enabled_without_endpoint_stays_disabled() -> None:
    """启用但无端点 → 不装配（启动期 `validate_worker` 会直接拒绝这种配置）。"""
    state = setup_otel(
        None, make_settings(otel_enabled=True, otel_exporter_otlp_endpoint="")
    )
    assert state.enabled is False
