"""M-C 数据连接 — ClickHouse 探针（HTTP 接口 + SELECT 1）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / FR-05
覆盖 FR：FR-05（安全连通性测试）
对应 AC：AC-02（失败信息脱敏：消息只含 host:port，绝不含密码 / 服务端原文）

实现口径：ClickHouse 的连通性以「HTTP 接口能执行一条只读查询」为准，
故直接 `POST /?query=SELECT 1`（走 8123），而非 native 9000 —— 见
`connections/clickhouse.py` 的选型说明（零新增依赖）。
"""
from __future__ import annotations

import httpx

from etl_agent.connections.clickhouse import CLICKHOUSE_HTTP_PORT, clickhouse_base_url
from etl_agent.connections.probes.base import ProbeConfig, ProbeError

# 探针超时：与 MySQL / PostgreSQL 探针的 connect_timeout 对齐（10s）
_PROBE_TIMEOUT_SECONDS = 10


async def probe_clickhouse(config: ProbeConfig) -> None:
    """HTTP 接口 + `SELECT 1`（FR-05）。

    Raises:
        ProbeError: 连接失败或查询失败（消息脱敏后由 probe.py 落状态）。
    """
    if not config.host:
        raise ProbeError("ClickHouse 探针需要 host")
    scheme = (config.extra or {}).get("scheme", "http")
    port = config.port or CLICKHOUSE_HTTP_PORT
    base_url = clickhouse_base_url(config.host, port, scheme=scheme)
    params: dict[str, str] = {"query": "SELECT 1"}
    # 指定默认库：库名非法/不存在时 ClickHouse 会直接报错，属有效的连通性信号
    if config.database_name:
        params["database"] = config.database_name

    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS) as client:
            response = await client.post(
                base_url,
                params=params,
                auth=(config.username, config.password),
            )
    except (httpx.HTTPError, OSError) as exc:
        raise ProbeError(f"clickhouse connect failed at {config.host}:{port}") from exc

    if response.status_code != httpx.codes.OK:
        # 只报状态码，不回传服务端原文：响应体可能带库名/账号提示（AC-02 脱敏口径）
        raise ProbeError(
            f"clickhouse probe query failed at {config.host}:{port} (HTTP {response.status_code})"
        )
