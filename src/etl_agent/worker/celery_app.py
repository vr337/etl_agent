"""M-J/M-K 执行 Worker — Celery 应用装配（Broker / 结果后端 / 序列化 / 至少一次投递）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 4.3 章进程与入口 / 第 8 章「高可用 · Worker 可靠性」/ ADR-06
覆盖 FR：FR-38（Celery Worker 受管执行；`task_acks_late` + `worker_prefetch_multiplier=1` + `task_reject_on_worker_lost` 保证至少一次投递）
对应 AC：AC-06（Worker 调度 SeaTunnel 的可靠投递前置）

可靠性三件套（SPEC 8「高可用」行）：
- `task_acks_late=True`：任务执行完后才确认，进程崩溃不丢已授权执行；
- `worker_prefetch_multiplier=1`：一次只取一条，防大任务阻塞后续（饥饿）；
- `task_reject_on_worker_lost=True`：Worker 失联即重投，由消费侧幂等兜底
  （Replay Guard + 稳定 SeaTunnel JobId + retry 追溯，FR-34/42）。

Broker 与结果后端同为 Redis（SPEC 2.1：Celery Broker、结果后端、网关 Nonce 一器三用）。
"""
from __future__ import annotations

import logging

from celery import Celery
from celery.schedules import schedule as celery_schedule
from celery.signals import worker_ready

from etl_agent.config import settings

logger = logging.getLogger("etl_agent.worker")

app = Celery("etl_agent", broker=settings.resolved_redis_url, backend=settings.resolved_redis_url)
app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
)


@worker_ready.connect
def _log_engine_mode(**_kwargs: object) -> None:
    """Worker 就绪时打印引擎通道选型（F5：可审、可关）。

    用 `worker_ready` 而非 `worker_init`：后者触发时 celery 日志系统尚未装配，
    INFO 行会被丢弃（2026-10-16 首版实证）。就绪时打，日志稳定落盘。
    engine_mode=real 时附引擎地址，便于核对「演示声称 mock」与「worker 实际
    通道」是否一致——两者分属不同进程配置，本行与 `docker exec env` 互为锚点。
    """
    if settings.is_mock_engine:
        logger.info("worker 就绪：engine_mode=mock（演示态合成指标：check/submit 为 mock，其余链路真实执行）")
    else:
        logger.info("worker 就绪：engine_mode=real（受管 SeaTunnel 执行：url=%s）", settings.seatunnel_url)


__all__ = ["app", "celery_schedule"]
