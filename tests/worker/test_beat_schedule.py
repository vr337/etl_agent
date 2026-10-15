"""Celery 应用与 Beat 计划用例（T-13.1 / T-14.1 / FR-35/38/40 / FR-10）。

覆盖：可靠性三件套（acks_late / prefetch=1 / reject_on_worker_lost）、
四类周期任务的注册名与周期、任务注册完整性。
"""
from __future__ import annotations

from etl_agent.config import settings as default_settings
from etl_agent.worker import app as worker_app
from etl_agent.worker.beat_schedule import (
    TASK_CREDENTIAL_SCAN,
    TASK_HEARTBEAT,
    TASK_SCAN_OUTBOX,
    TASK_SCHEDULE_SCAN,
)


def test_reliability_triplet() -> None:
    """至少一次投递三件套（SPEC 8「高可用 · Worker 可靠性」/ ADR-06）。"""
    conf = worker_app.conf
    assert conf.task_acks_late is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_reject_on_worker_lost is True


def test_serialization_and_timezone() -> None:
    """JSON 序列化 + Asia/Shanghai 时区（SPEC 4.3 / FR-40 时区语义）。"""
    conf = worker_app.conf
    assert conf.task_serializer == "json"
    assert conf.result_serializer == "json"
    assert conf.accept_content == ["json"]
    assert conf.timezone == "Asia/Shanghai"


def test_beat_schedule_has_four_tasks() -> None:
    """四类周期任务就位，注册名与任务模块一致（SPEC 4.3）。"""
    schedule = worker_app.conf.beat_schedule
    assert set(schedule) == {"outbox-scan", "worker-heartbeat", "credential-scan", "schedule-scan"}
    assert schedule["outbox-scan"]["task"] == TASK_SCAN_OUTBOX
    assert schedule["worker-heartbeat"]["task"] == TASK_HEARTBEAT
    assert schedule["credential-scan"]["task"] == TASK_CREDENTIAL_SCAN
    assert schedule["schedule-scan"]["task"] == TASK_SCHEDULE_SCAN


def test_beat_intervals_match_config() -> None:
    """周期取自配置（默认 2s / 10s / 3600s / 30s，SPEC 4.3 表）。"""
    schedule = worker_app.conf.beat_schedule
    assert schedule["outbox-scan"]["schedule"].run_every.total_seconds() == (
        default_settings.outbox_scan_interval_seconds
    )
    assert schedule["worker-heartbeat"]["schedule"].run_every.total_seconds() == (
        default_settings.worker_heartbeat_interval_seconds
    )
    assert schedule["credential-scan"]["schedule"].run_every.total_seconds() == (
        default_settings.credential_scan_interval_seconds
    )
    assert schedule["schedule-scan"]["schedule"].run_every.total_seconds() == (
        default_settings.scheduler_scan_interval_seconds
    )
    assert default_settings.outbox_scan_interval_seconds == 2
    assert default_settings.worker_heartbeat_interval_seconds == 10
    assert default_settings.credential_scan_interval_seconds == 3600
    assert default_settings.scheduler_scan_interval_seconds == 30


def test_all_four_tasks_registered() -> None:
    """任务模块导入即注册（Beat 以注册名派发，缺一个就是静默不跑）。"""
    registered = set(worker_app.tasks)
    assert TASK_SCAN_OUTBOX in registered
    assert TASK_HEARTBEAT in registered
    assert TASK_CREDENTIAL_SCAN in registered
    assert TASK_SCHEDULE_SCAN in registered
