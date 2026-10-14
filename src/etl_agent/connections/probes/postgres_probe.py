"""M-C 数据连接 — PostgreSQL 探针（asyncpg + SELECT 1）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / FR-05
覆盖 FR：FR-05（安全连通性测试）
对应 AC：AC-02（失败信息脱敏）
"""
from __future__ import annotations

import asyncpg

from etl_agent.connections.probes.base import ProbeConfig, ProbeError


async def probe_postgres(config: ProbeConfig) -> None:
    """asyncpg 连接 + SELECT 1（FR-05）。

    Raises:
        ProbeError: 连接失败或查询失败（消息脱敏后由 probe.py 落状态）。
    """
    if not config.host or config.port is None or not config.database_name:
        raise ProbeError("PostgreSQL 探针需要 host/port/database_name")
    try:
        conn = await asyncpg.connect(
            host=config.host,
            port=config.port,
            user=config.username,
            password=config.password,
            database=config.database_name,
            timeout=10,
        )
    except (asyncpg.PostgresError, OSError) as exc:
        # 只透出主机/端口级信息；asyncpg 异常不回传密码，仍由 probe.py 统一 mask 兜底
        raise ProbeError(f"postgres connect failed at {config.host}:{config.port}") from exc
    try:
        await conn.execute("SELECT 1")
    except (asyncpg.PostgresError, OSError) as exc:
        raise ProbeError(f"postgres probe query failed at {config.host}:{config.port}") from exc
    finally:
        await conn.close()
