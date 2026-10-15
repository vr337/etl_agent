"""M-J 执行 Worker — Worker 心跳任务（10s 上报 / 35s 判失活，FR-38）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 5.2 章 worker_heartbeats / 第 8 章「高可用」行
覆盖 FR：FR-38（心跳上报与能力声明；35s 判失活）
对应 AC：AC-06（Worker 健康可观测，/health 摘要数据源）

失活判定（SPEC 8 高可用行）：`now - last_heartbeat_at > worker_inactive_seconds`
(默认 35s) 即失活；失活 Worker 的执行进入可诊断状态（S8：不自动重放，防重复副作用）。
心跳行以 worker_id 为主键 upsert（幂等，重复上报不累积行）。
"""
from __future__ import annotations

import asyncio
import os
import socket
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.harness.side_effects.registry import ALLOWED_COMMANDS, MAIN_EXECUTION_COMMAND
from etl_agent.infrastructure.models import WorkerHeartbeat
from etl_agent.worker.celery_app import app


def worker_identity() -> str:
    """Worker 身份（主机名 + PID；容器重建后自然换身份，旧行由失活判定淘汰）。"""
    return f"{socket.gethostname()}-{os.getpid()}"


def worker_capabilities() -> dict[str, object]:
    """能力声明（只声明命令清单与引擎类型，不含任何环境秘密，AC-02）。"""
    return {
        "commands": sorted(ALLOWED_COMMANDS),
        "main_command": MAIN_EXECUTION_COMMAND,
        "engine": "seatunnel",
    }


async def report_heartbeat(session: AsyncSession, *, worker_id: str | None = None) -> str:
    """上报一次心跳（upsert；返回 worker_id）。"""
    identity = worker_id or worker_identity()
    now = datetime.now(UTC)
    stmt = (
        pg_insert(WorkerHeartbeat)
        .values(worker_id=identity, last_heartbeat_at=now, capabilities=worker_capabilities())
        .on_conflict_do_update(
            index_elements=[WorkerHeartbeat.worker_id],
            set_={"last_heartbeat_at": now, "capabilities": worker_capabilities()},
        )
    )
    await session.execute(stmt)
    await session.flush()
    return identity


async def _run() -> str:
    from etl_agent.worker.session import worker_session

    async with worker_session() as session:
        identity = await report_heartbeat(session)
        await session.commit()
        return identity


@app.task(name="worker.heartbeat", bind=True, acks_late=True)
def heartbeat_task(self: object) -> str:
    """Celery 入口（Beat 10s；薄壳：开会话 → 上报 → 提交）。"""
    return asyncio.run(_run())


__all__ = ["heartbeat_task", "report_heartbeat", "worker_capabilities", "worker_identity"]
