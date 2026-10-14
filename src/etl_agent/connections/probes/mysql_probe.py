"""M-C 数据连接 — MySQL 探针（aiomysql + SELECT 1）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / FR-05
覆盖 FR：FR-05（安全连通性测试）
对应 AC：AC-02（失败信息脱敏）
"""
from __future__ import annotations

import aiomysql

from etl_agent.connections.probes.base import ProbeConfig, ProbeError


async def probe_mysql(config: ProbeConfig) -> None:
    """aiomysql 连接 + SELECT 1（FR-05）。

    Raises:
        ProbeError: 连接失败或查询失败（消息脱敏后由 probe.py 落状态）。
    """
    if not config.host or config.port is None or not config.database_name:
        raise ProbeError("MySQL 探针需要 host/port/database_name")
    try:
        conn = await aiomysql.connect(
            host=config.host,
            port=config.port,
            user=config.username,
            password=config.password,
            db=config.database_name,
            connect_timeout=10,
        )
    except (aiomysql.Error, OSError) as exc:
        raise ProbeError(f"mysql connect failed at {config.host}:{config.port}") from exc
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT 1")
            await cursor.fetchone()
    except (aiomysql.Error, OSError) as exc:
        raise ProbeError(f"mysql probe query failed at {config.host}:{config.port}") from exc
    finally:
        conn.close()
