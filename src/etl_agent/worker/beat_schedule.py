"""M-J/M-K 执行 Worker — Celery Beat 周期任务配置（4 类，SPEC 4.3）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 4.3 章进程与入口 / 第 8 章「高可用」行
覆盖 FR：FR-38（Worker 心跳 10s）、FR-10（凭据告警扫描 3600s）、FR-40（调度扫描 30s）、FR-35（Outbox 扫描 2s）
对应 AC：AC-06（Beat 调度就位；部署形态单实例，Q11/S-Q3 待决议多实例）

四类周期任务（SPEC 4.3 Beat 行）：
| 任务 | 周期 | 来源 |
|------|------|------|
| Outbox 扫描 / 执行推进 | 2s | FR-35 / ADR-06 |
| Worker 心跳 | 10s | FR-38 |
| 凭据告警扫描 | 3600s | FR-10 |
| 调度扫描 | 30s | FR-40 |

周期取自 `config.py` 的 S7 配置（`ETL_AGENT_*_INTERVAL_SECONDS`），启动期由
`validate_worker()` 保证为正数——零 / 负间隔会让任务永不触发或忙轮询。

任务以**注册名**（字符串）装配而非导入函数对象：Beat 与 Worker 是不同进程，
用名字解耦可避免「Beat 侧导入失败 → 整个 beat 起不来」；任务模块由
`etl_agent.worker` 包入口统一导入完成注册。

部署形态：单实例 Beat（提示词 §8.7：Q11/S-Q3 待决议多实例 + 时钟源）；
多实例改造时替换调度存储（如 redbeat）即可，任务注册名不变。
"""
from __future__ import annotations

from celery.schedules import schedule as celery_schedule

from etl_agent.config import settings
from etl_agent.worker.celery_app import app

# 任务注册名（与各任务模块 @app.task(name=...) 一致；改名必须同步此处）
TASK_SCAN_OUTBOX = "worker.scan_outbox"
TASK_HEARTBEAT = "worker.heartbeat"
TASK_CREDENTIAL_SCAN = "worker.credential_scan"
TASK_SCHEDULE_SCAN = "worker.schedule_scan"

app.conf.beat_schedule = {
    "outbox-scan": {
        "task": TASK_SCAN_OUTBOX,
        "schedule": celery_schedule(run_every=settings.outbox_scan_interval_seconds),
    },
    "worker-heartbeat": {
        "task": TASK_HEARTBEAT,
        "schedule": celery_schedule(run_every=settings.worker_heartbeat_interval_seconds),
    },
    "credential-scan": {
        "task": TASK_CREDENTIAL_SCAN,
        "schedule": celery_schedule(run_every=settings.credential_scan_interval_seconds),
    },
    "schedule-scan": {
        "task": TASK_SCHEDULE_SCAN,
        "schedule": celery_schedule(run_every=settings.scheduler_scan_interval_seconds),
    },
}

__all__ = [
    "TASK_CREDENTIAL_SCAN",
    "TASK_HEARTBEAT",
    "TASK_SCAN_OUTBOX",
    "TASK_SCHEDULE_SCAN",
]
