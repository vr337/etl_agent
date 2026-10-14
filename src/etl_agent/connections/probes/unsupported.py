"""M-C 数据连接 — 暂不支持连接器的明确错误探针。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / S3 禁止事项 15
覆盖 FR：FR-05（连通性测试对暂不支持类型返回明确错误）
对应 AC：AC-02（错误信息明确但不含任何连接参数细节）

Oracle / SQL Server / Doris / Hive / HDFS 的驱动安装复杂或依赖外部环境（Q2/Q8 待定），
S3 统一返回明确错误而非静默失败——调用方可以区分「测过但失败」与「压根没测」。
"""
from __future__ import annotations

from etl_agent.connections.probes.base import (
    ProbeConfig,
    ProbeError,
    ProbeUnsupported,
    unsupported_error,
)


async def probe_unsupported(config: ProbeConfig) -> None:
    """对暂不支持探查的连接器类型抛出明确错误（S3 禁止事项 15）。

    Raises:
        ProbeUnsupported: 恒定抛出（connector type not yet supported for probing）。
    """
    raise unsupported_error(config.connector_type)


__all__ = ["ProbeConfig", "ProbeError", "ProbeUnsupported", "probe_unsupported"]
