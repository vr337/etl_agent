"""M-J 执行 Worker — 调度扫描任务（30s，FR-40）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 4.3 章进程与入口 / 第 8 章「高可用 · 调度扫描 30s」/ 第 3.2 章 M-K
覆盖 FR：FR-40（扫描间隔默认 30s；cron + 时区；misfire SKIP/FIRE_ONCE 宽限 120s）、FR-41（水印推进）
对应 AC：AC-06（调度到期触发经 Harness 准备单通道，不绕过授权）

任务薄壳：开会话 → `scheduling.service.scan_due_schedules` → 提交。
到期触发的副作用出口仍是 Harness（Prepare → 审批 → Commit），本任务只做「时钟」。
"""
from __future__ import annotations

import asyncio

from etl_agent.scheduling.service import scan_due_schedules
from etl_agent.worker.celery_app import app


async def _run() -> dict[str, object]:
    from etl_agent.config import settings
    from etl_agent.worker.session import worker_session

    async with worker_session() as session:
        summary = await scan_due_schedules(session, settings=settings)
        await session.commit()
        return {
            "evaluated": summary.evaluated,
            "fired": summary.fired,
            "skipped": summary.skipped,
            "deferred": summary.deferred,
            "details": summary.details,
        }


@app.task(name="worker.schedule_scan", bind=True, acks_late=True)
def schedule_scan_task(self: object) -> dict[str, object]:
    """Celery 入口（Beat 30s；返回本遍评估 / 触发计数摘要）。"""
    return asyncio.run(_run())


__all__ = ["scan_due_schedules", "schedule_scan_task"]
