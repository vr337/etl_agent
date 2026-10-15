"""M-O 可观测性 — OpenTelemetry 初始化（四组件埋点，FR-60）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-O / 第 8 章「可观测」行 / 第 4.3 章进程与入口
覆盖 FR：FR-60（OpenTelemetry 埋点：FastAPI / HTTPX / SQLAlchemy / Celery）
对应 AC：AC-11（可关闭：`ETL_AGENT_OTEL_ENABLED=false` 时零成本不埋点不导出）

四组件与它们的「为什么」：

| 组件 | 埋点位置 | 覆盖的链路 |
|------|----------|------------|
| FastAPI | 控制面入站 | 请求 → 处理 → 响应（含 4xx/5xx 的 trace_id） |
| HTTPX | 出站 HTTP | 控制面 → Vault / LLM / SeaTunnel 的调用 |
| SQLAlchemy | 业务库 | 每次 DB 往返（含 Worker 侧） |
| Celery | 任务 | Beat → Worker 的跨进程链路（**断链修复点**：没有它，
  「请求里发起、Worker 里执行」就断成两段互不相关的 trace） |

**可关闭**：`otel_enabled=false` 时不构造 TracerProvider、不注册任何 instrumentor、
不建导出器——关闭路径不产生任何网络与线程开销。

**全局单例约束**：OTel 的 TracerProvider 一个进程只能设置一次，故本模块用
`_STATE` 做幂等守卫；重复调用返回首次的装配结果（测试与应用生命周期可安全重入）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from etl_agent.config import Settings
from etl_agent.config import settings as default_settings
from etl_agent.infrastructure.database import engine as business_engine
from etl_agent.observability.exporters import build_span_exporter
from etl_agent.observability.logging import configure_logging

# 服务名（OTLP resource 属性：采集端按此聚合）
SERVICE_NAME_VALUE = "etl_agent"

# 四组件标识（装配结果对测试与 /health 可见）
COMPONENT_FASTAPI = "fastapi"
COMPONENT_HTTPX = "httpx"
COMPONENT_SQLALCHEMY = "sqlalchemy"
COMPONENT_CELERY = "celery"

ALL_COMPONENTS: tuple[str, ...] = (
    COMPONENT_FASTAPI,
    COMPONENT_HTTPX,
    COMPONENT_SQLALCHEMY,
    COMPONENT_CELERY,
)


@dataclass(frozen=True)
class OtelState:
    """装配结果（`enabled=False` 时 `components` 为空——关闭即不埋点）。"""

    enabled: bool
    components: tuple[str, ...] = field(default_factory=tuple)


_STATE: OtelState | None = None
_PROVIDER: TracerProvider | None = None


def build_tracer_provider(
    settings: Settings, *, exporter: SpanExporter | None = None
) -> TracerProvider | None:
    """按配置构造 TracerProvider（关闭或端点缺失时返回 None）。

    与 `setup_otel` 分离的原因：OTel 的全局 TracerProvider 一个进程只能设置一次，
    而「能否正确构造并导出」这件事需要可重复验证——本函数不做任何全局注册，
    调用方（测试）可以用它拿到 provider 并显式取 tracer，验证导出链路。
    """
    if not settings.otel_enabled:
        return None
    active_exporter = exporter or build_span_exporter(settings)
    if active_exporter is None:
        return None
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: SERVICE_NAME_VALUE}))
    provider.add_span_processor(BatchSpanProcessor(active_exporter))
    return provider


def setup_otel(
    app: FastAPI | None = None,
    settings: Settings | None = None,
    *,
    exporter: SpanExporter | None = None,
) -> OtelState:
    """装配 OTel（幂等：重复调用返回首次结果）。

    Args:
        app: FastAPI 应用（None 时只装配 Worker / 库 / 出站三类组件）。
        settings: 应用配置；缺省全局配置。
        exporter: 显式注入的导出器（测试用内存导出器；缺省按配置构造 OTLP）。

    Returns:
        OtelState: 是否启用与已装配的组件集合。
    """
    global _STATE, _PROVIDER

    effective = settings or default_settings
    configure_logging()
    if _STATE is not None:
        return _STATE

    provider = build_tracer_provider(effective, exporter=exporter)
    if provider is None:
        # 关闭即零成本：不建 Provider、不注册 instrumentor、不建导出器
        _STATE = OtelState(enabled=False)
        return _STATE

    trace.set_tracer_provider(provider)
    _PROVIDER = provider

    components: list[str] = []
    HTTPXClientInstrumentor().instrument()
    components.append(COMPONENT_HTTPX)
    SQLAlchemyInstrumentor().instrument(engine=business_engine.sync_engine)
    components.append(COMPONENT_SQLALCHEMY)
    CeleryInstrumentor().instrument()
    components.append(COMPONENT_CELERY)
    if app is not None:
        FastAPIInstrumentor.instrument_app(app)
        components.append(COMPONENT_FASTAPI)

    _STATE = OtelState(enabled=True, components=tuple(sorted(components)))
    return _STATE


def shutdown_otel() -> None:
    """关闭 TracerProvider（冲刷未导出的 span；应用关闭时调用）。"""
    global _STATE, _PROVIDER

    if _PROVIDER is not None:
        _PROVIDER.shutdown()
    _PROVIDER = None
    _STATE = None


def otel_state() -> OtelState:
    """当前装配结果（未装配时为关闭态）。"""
    return _STATE or OtelState(enabled=False)


__all__ = [
    "ALL_COMPONENTS",
    "COMPONENT_CELERY",
    "COMPONENT_FASTAPI",
    "COMPONENT_HTTPX",
    "COMPONENT_SQLALCHEMY",
    "SERVICE_NAME_VALUE",
    "OtelState",
    "build_tracer_provider",
    "otel_state",
    "setup_otel",
    "shutdown_otel",
]
