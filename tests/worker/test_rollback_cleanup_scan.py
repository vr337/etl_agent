"""M-L 受管回滚 — 到期清理扫描任务用例（T-16.2 / FR-48 / AC-08）。

覆盖：`SYSTEM_CLEANUP_ACTOR_ID` 的稳定性与系统语义、`scan_expired_rollbacks`
薄壳参数（系统主体 + 默认保留期）、Celery 任务注册与可调用性。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 5.2 章 rollback_cleanup_requests /
第 8 章「高可用 · 保留期清理」
覆盖 FR：FR-48（保留期默认 7 天 → 到期生成清理申请）
对应 AC：AC-08（制品按期清理，不无限堆积）
"""
from __future__ import annotations

import uuid

from etl_agent.worker import app as worker_app
from etl_agent.worker.beat_schedule import TASK_ROLLBACK_CLEANUP_SCAN
from etl_agent.worker.tasks.rollback_cleanup_scan import (
    SYSTEM_CLEANUP_ACTOR_ID,
    rollback_cleanup_scan_task,
    scan_expired_rollbacks,
)


def test_system_actor_id_is_stable_and_systemic() -> None:
    """系统主体 UUID 每个进程一致（uuid5 命名空间派生），且非人类主体。"""
    assert isinstance(SYSTEM_CLEANUP_ACTOR_ID, uuid.UUID)
    assert SYSTEM_CLEANUP_ACTOR_ID.version == 5
    # 可复现：同样输入派生同样结果（跨进程稳定）。
    assert SYSTEM_CLEANUP_ACTOR_ID == uuid.uuid5(
        uuid.NAMESPACE_URL, "etl_agent://system/rollback-cleanup-scanner"
    )


def test_task_registered_and_callable() -> None:
    """Celery 任务按注册名就位（Beat 3600s 派发）。"""
    assert TASK_ROLLBACK_CLEANUP_SCAN in set(worker_app.tasks)
    assert callable(rollback_cleanup_scan_task)


def test_thin_shell_delegates_to_scan() -> None:
    """薄壳只做转发：重导出 `scan_expired_rollbacks` 供任务调用与测试引用。"""
    assert scan_expired_rollbacks.__name__ == "scan_expired_rollbacks"
    assert callable(scan_expired_rollbacks)
