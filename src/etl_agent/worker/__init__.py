"""M-J/M-K 执行 Worker — 包入口（Celery 应用 + Beat 计划 + 任务注册）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J/M-K / 第 4.3 章进程与入口 / 第 8 章「高可用 · Worker 可靠性」
覆盖 FR：FR-38（Worker 受管执行）、FR-40（调度）、FR-35（Outbox 可靠投递）
对应 AC：AC-06（Worker 调度 SeaTunnel + 影子表 + 质量分流）

模块边界（SPEC 3.2 M-J 边界说明）：Worker 是控制面之外唯一持令牌本体与物化凭据的
进程；所有副作用仍以 Harness 注册清单判定（S6 Tool Broker），Worker 只是执行通道——
不自行扩大可执行集合（禁止事项 19：Worker 与 API 同版本部署，ADR-02）。

导入顺序即装配顺序：`app`（Celery 实例）→ 任务模块（`@app.task` 注册）→ `beat_schedule`
（周期计划）。Celery 以 `-A etl_agent.worker` 启动，本包入口是唯一装配点。
"""
from __future__ import annotations

from etl_agent.worker import beat_schedule  # noqa: F401  -- 导入副作用：装配 beat_schedule
from etl_agent.worker.celery_app import app

# 任务模块导入即完成 @app.task 注册（Beat 以注册名装配，见 beat_schedule）
from etl_agent.worker.tasks import (  # noqa: F401  -- 导入副作用：注册四类周期任务
    consume_outbox,
    credential_scan,
    heartbeat,
    schedule_scan,
)

__all__ = ["app"]
