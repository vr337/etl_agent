"""M-O 可观测性 — OTLP 导出通道（超时 10s；可关闭，FR-60）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-O / 第 8 章「可观测」行
覆盖 FR：FR-60（OTLP 导出；`ETL_AGENT_OTEL_ENABLED=false` 时不上报）
对应 AC：AC-11（未配置端点时不静默丢弃遥测——启用即必须给端点，启动期拦截）

两条硬约束：
1. **导出超时 10s**（`ETL_AGENT_OTEL_EXPORT_TIMEOUT_SECONDS`）：导出不得阻塞请求路径，
   采集端抖动不能变成控制面延迟；
2. **可关闭**：`otel_enabled=false` 时返回 None，不建任何导出器、不发任何网络请求——
   「关掉」必须是零成本，而不是「发了但没人收」。
"""
from __future__ import annotations

from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import SpanExporter

from etl_agent.config import Settings

# OTLP 端点协议前缀（gRPC 通道；http/protobuf 由端点前缀区分）
GRPC_SCHEME = "grpc"
HTTP_SCHEME = "http"


def build_span_exporter(settings: Settings) -> SpanExporter | None:
    """按配置构造 OTLP span 导出器（关闭或未配置端点时返回 None）。

    Args:
        settings: 应用配置（端点与超时来源）。

    Returns:
        SpanExporter | None: OTLP gRPC 导出器；`otel_enabled=false` 时为 None。
    """
    endpoint = settings.otel_exporter_otlp_endpoint.strip()
    if not settings.otel_enabled or not endpoint:
        return None
    normalized = endpoint
    if not normalized.startswith(("http://", "https://")):
        # 允许只填 host:port（OTLP 惯用形态），补默认非加密 gRPC 前缀
        normalized = f"http://{normalized}"
    insecure = not normalized.startswith("https://")
    return OTLPSpanExporter(
        endpoint=normalized,
        insecure=insecure,
        timeout=settings.otel_export_timeout_seconds,
    )


__all__ = ["GRPC_SCHEME", "HTTP_SCHEME", "build_span_exporter"]
