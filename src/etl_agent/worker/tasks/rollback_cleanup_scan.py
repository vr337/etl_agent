"""M-L 受管回滚 — Worker 回滚制品到期清理扫描任务（3600s，FR-48）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 4.3 章进程与入口 /
第 5.2 章 rollback_cleanup_requests / 第 8 章「高可用 · 保留期清理」
覆盖 FR：FR-48（保留期默认 7 天 → 到期生成清理申请）
对应 AC：AC-08（制品按期清理，不无限堆积）

任务薄壳：开会话 → `cleanup.scan_expired_rollbacks` → 提交。
**只生成清理申请，不执行数据面动作**（禁止事项 17：清理仍走 Harness 协议，
PDP 默认 P0 → 双人审批 → Commit → Outbox → Worker）。
"""
from __future__ import annotations

import asyncio
import uuid

from etl_agent.rollback.cleanup import scan_expired_rollbacks
from etl_agent.worker.celery_app import app

# 系统主体标识（清理申请由 Worker 登记，非人类用户）。
# `rollback_cleanup_requests.requested_by` 是无外键的 UUID 列，故用固定命名空间 UUID
# 表达「系统发起」——稳定、可复现、不依赖库中预置行（审批仍由人类完成，见 FR-48）。
SYSTEM_CLEANUP_ACTOR_ID = uuid.uuid5(
    uuid.NAMESPACE_URL, "etl_agent://system/rollback-cleanup-scanner"
)


async def _run() -> dict[str, object]:
    from etl_agent.worker.session import worker_session

    async with worker_session() as session:
        summary = await scan_expired_rollbacks(
            session,
            requested_by=SYSTEM_CLEANUP_ACTOR_ID,
            retention_days=None,
        )
        await session.commit()
        return {
            "evaluated": summary.evaluated,
            "created": summary.created,
            "skipped": summary.skipped,
            "capacity_warning": summary.retention.capacity_warning,
            "retention": summary.retention.summary(),
            "details": list(summary.details),
        }


@app.task(name="worker.rollback_cleanup_scan", bind=True, acks_late=True)
def rollback_cleanup_scan_task(self: object) -> dict[str, object]:
    """Celery 入口（Beat 3600s；返回本遍建单摘要）。"""
    return asyncio.run(_run())


__all__ = ["SYSTEM_CLEANUP_ACTOR_ID", "rollback_cleanup_scan_task", "scan_expired_rollbacks"]
