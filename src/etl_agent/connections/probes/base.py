"""M-C 数据连接 — 探针接口与通用错误。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / FR-05
覆盖 FR：FR-05（安全连通性测试）
对应 AC：AC-02（探针失败错误摘要脱敏：不含密码）
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from etl_agent.connections.errors import ConnectorUnsupported, ProbeFailed

ProbeFn = Callable[["ProbeConfig"], Awaitable[None]]


@dataclass(frozen=True)
class ProbeConfig:
    """探针输入：连接定位参数 + 内存中短暂持有的解密凭据（禁止事项 16）。"""

    connector_type: str
    host: str
    port: int | None
    database_name: str | None
    username: str
    password: str
    extra: dict[str, str] | None = None


class ProbeError(Exception):
    """探针失败基类：message 必须脱敏（由 probe.py 统一 mask 后落状态/日志）。"""


class ProbeUnsupported(ProbeError):
    """连接器类型暂不支持探查（S3 禁止事项 15：返回明确错误，非静默失败）。"""


def unsupported_error(connector_type: str) -> ProbeUnsupported:
    """明确的「暂不支持」错误（Oracle/SQLServer/Doris/Hive/HDFS）。"""
    return ProbeUnsupported(f"connector type not yet supported for probing: {connector_type}")


def as_api_error(connector_type: str, exc: ProbeError) -> Exception:
    """探针异常 → API 错误码映射（unsupported → 422 CONNECTOR_UNSUPPORTED；其余 → 422 PROBE_FAILED）。"""
    if isinstance(exc, ProbeUnsupported):
        return ConnectorUnsupported(str(exc))
    return ProbeFailed(str(exc))


async def probe_connection(config: ProbeConfig) -> None:
    """按连接器类型执行探针（供 probes/__init__.probe_for 分发）。"""
    from etl_agent.connections.probes import probe_for

    await probe_for(config.connector_type)(config)
