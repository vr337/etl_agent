"""M-J 执行 Worker — 凭据到期扫描任务（3600s，FR-10）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / 第 8 章「性能 · 周期任务」
覆盖 FR：FR-10（凭据账龄两级预警 WARNING/OVERDUE + 去重键）
对应 AC：AC-02（账龄取自 Secret 行时间戳，无需解密明文）

S3 已实现扫描逻辑（`secrets.expiry.scan_credentials`，CLI 手动触发）；
本任务只是它的 Beat 周期包装（S3 禁止事项 14：S3 不起 Beat，S7 才接）。
"""
from __future__ import annotations

import asyncio

from etl_agent.secrets.expiry import scan_credentials
from etl_agent.worker.celery_app import app


async def _run() -> int:
    from etl_agent.worker.session import worker_session

    async with worker_session() as session:
        alerts = await scan_credentials(session)
        await session.commit()
        return len(alerts)


@app.task(name="worker.credential_scan", bind=True, acks_late=True)
def credential_scan_task(self: object) -> int:
    """Celery 入口（Beat 3600s；返回新建预警数）。"""
    return asyncio.run(_run())


__all__ = ["credential_scan_task", "scan_credentials"]
