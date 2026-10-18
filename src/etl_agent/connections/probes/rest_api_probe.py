"""M-C 数据连接 — REST API 探针（GET 端点，只判状态码）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / FR-05
覆盖 FR：FR-05（安全连通性测试）
对应 AC：AC-02（失败摘要脱敏：只含 host:port 与状态码，不回传响应正文）

探针只发一次 GET、只看状态码，**不解析业务响应体** —— 有些 API 的根路径返回
HTML 或错误页，把「能取到 2xx」作为连通性判据比解析结构更稳，也避免把
业务错误误判成网络故障。响应正文一律丢弃（可能含账号信息）。
"""
from __future__ import annotations

import httpx

from etl_agent.connections.probes.base import ProbeConfig, ProbeError
from etl_agent.connections.rest_api import auth_headers, parse_endpoint

_PROBE_TIMEOUT_SECONDS = 15


async def probe_rest_api(config: ProbeConfig) -> None:
    """GET Base URL（+ 资源路径），2xx 视为连通（FR-05）。

    Raises:
        ProbeError: 定位参数非法 / 网络不可达 / 非 2xx（消息已脱敏）。
    """
    try:
        endpoint = parse_endpoint(config.host, config.port, config.database_name)
    except ValueError as exc:
        raise ProbeError(str(exc)) from exc

    headers = auth_headers({"username": config.username, "password": config.password})
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS) as client:
            response = await client.get(endpoint.url, headers=headers)
    except (httpx.HTTPError, OSError) as exc:
        raise ProbeError(f"rest connect failed at {endpoint.host_label}") from exc

    if not 200 <= response.status_code < 300:
        raise ProbeError(
            f"rest probe failed at {endpoint.host_label} (HTTP {response.status_code})"
        )
