"""M-C 数据连接 — 连接器探针包初始化与注册表。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / FR-05
覆盖 FR：FR-05（安全连通性测试）
对应 AC：AC-02（探针失败错误摘要脱敏）
"""
from __future__ import annotations

from etl_agent.connections.probes.base import (
    ProbeConfig,
    ProbeError,
    ProbeFn,
    ProbeUnsupported,
    probe_connection,
)
from etl_agent.connections.probes.clickhouse_probe import probe_clickhouse
from etl_agent.connections.probes.local_file_probe import probe_local_file
from etl_agent.connections.probes.mysql_probe import probe_mysql
from etl_agent.connections.probes.postgres_probe import probe_postgres
from etl_agent.connections.probes.unsupported import probe_unsupported

__all__ = [
    "PROBE_SUPPORTED_TYPES",
    "ProbeConfig",
    "ProbeError",
    "ProbeUnsupported",
    "probe_connection",
    "probe_for",
]

# 实装的探针（禁止事项 15：Oracle/SQLServer/Doris/Hive/HDFS 走 unsupported）
# S11 T-21：ClickHouse 走 HTTP 接口（8123）实探，见 clickhouse_probe 的选型说明。
_PROBES = {
    "POSTGRESQL": probe_postgres,
    "MYSQL": probe_mysql,
    "CLICKHOUSE": probe_clickhouse,
    "LOCAL_FILE": probe_local_file,
}

PROBE_SUPPORTED_TYPES = frozenset(_PROBES)


def probe_for(connector_type: str) -> ProbeFn:
    """按连接器类型取探针函数；未实装类型返回明确错误探针（非静默失败）。"""
    return _PROBES.get(connector_type, probe_unsupported)
