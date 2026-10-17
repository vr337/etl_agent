"""M-C 数据连接 — ClickHouse HTTP 接口定位（探针与元数据探查共用）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / 第 6.1 章 API-03
覆盖 FR：FR-04（连接登记）、FR-05（安全连通性测试）、FR-12（表级只读 Profile）
对应 AC：AC-02（响应零明文 / 失败摘要脱敏）

**为什么走 HTTP 而不是 native 或 MySQL 协议**（S11 T-21 选型）：
- ClickHouse 的 native 协议（9000）需要 `clickhouse-driver` / `clickhouse-connect`
  新依赖；HTTP 接口（8123）用既有 `httpx` 即可，**零新增依赖**；
- ClickHouse 的 MySQL 兼容协议（9004）默认关闭、且不保证开启，不可作为默认通道；
- HTTP 接口同时支持 Basic 认证与 `param_*` 命名参数，探针与 `system.columns`
  元数据探查可复用同一套会话实现。

**TLS**：连接登记模型（`data_connections`）暂无 scheme 列，故固定 `http`。
需要 TLS 时应由反向代理终止（8443 → 8123），另行排期（见 tracking T-S11-CH-TLS）。
"""
from __future__ import annotations

# ClickHouse HTTP 接口默认端口（native 协议为 9000，不要混用）
CLICKHOUSE_HTTP_PORT = 8123

# 允许的 scheme（当前固定 http；显式白名单防止把用户输入拼进 URL 变成 SSRF 通道）
_ALLOWED_SCHEMES = frozenset({"http", "https"})


def clickhouse_base_url(host: str, port: int | None, *, scheme: str = "http") -> str:
    """拼装 ClickHouse HTTP 接口基址（`{scheme}://{host}:{port}/`）。

    Args:
        host: 连接登记的主机名（已由登记校验为非空）。
        port: HTTP 端口；缺省取 8123。
        scheme: `http` / `https`（白名单外一律回落 `http`，不静默接受任意 scheme）。

    Returns:
        形如 `http://127.0.0.1:8123/` 的基址（**带尾斜杠**，便于直接拼查询串）。

    Raises:
        ValueError: host 为空（探针/探查前置校验的兜底，调用方应先给出可读错误）。
    """
    if not host:
        raise ValueError("ClickHouse 定位参数缺少 host")
    effective_scheme = scheme if scheme in _ALLOWED_SCHEMES else "http"
    effective_port = port or CLICKHOUSE_HTTP_PORT
    return f"{effective_scheme}://{host}:{effective_port}/"
