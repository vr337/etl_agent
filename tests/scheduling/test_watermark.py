"""水印增量用例（T-14.4 / FR-41）。

覆盖：窗口左闭右开、滞后窗右端 = now - lag、首次取 watermark_initial、
推进即取窗口右端、窗口事实可落 JSON 列、非增量模式不产生窗口。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from etl_agent.infrastructure.models import PipelineSchedule
from etl_agent.scheduling.service import (
    MODE_BATCH,
    MODE_WATERMARK_INCREMENTAL,
    build_watermark_window,
)
from etl_agent.scheduling.watermark import advance, resolve_window

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)


def test_window_uses_initial_when_no_previous() -> None:
    """首次执行：左端取 watermark_initial（FR-41）。"""
    window = resolve_window(
        column="updated_at", initial="2026-01-01T00:00:00+00:00", lag_seconds=300,
        previous=None, now=NOW,
    )
    assert window.start == "2026-01-01T00:00:00+00:00"
    assert window.end == (NOW - timedelta(seconds=300)).isoformat()
    assert window.lag_seconds == 300


def test_window_prefers_previous_over_initial() -> None:
    """有上一次推进值时以它为左端（不回到初始值，否则会重复搬运）。"""
    window = resolve_window(
        column="updated_at",
        initial="2026-01-01T00:00:00+00:00",
        lag_seconds=0,
        previous="2026-10-14T10:00:00+00:00",
        now=NOW,
    )
    assert window.start == "2026-10-14T10:00:00+00:00"
    assert window.end == NOW.isoformat()


def test_negative_lag_clamped_to_zero() -> None:
    """滞后为负按 0 处理（负滞后会让窗口右端超过 now，读到未提交数据）。"""
    window = resolve_window(
        column="ts", initial=None, lag_seconds=-60, previous=None, now=NOW
    )
    assert window.lag_seconds == 0
    assert window.end == NOW.isoformat()


def test_advance_returns_window_end() -> None:
    """推进即取窗口右端（下一次的 previous 就是它，无需额外状态列）。"""
    window = resolve_window(
        column="ts", initial="2026-01-01T00:00:00+00:00", lag_seconds=60, previous=None, now=NOW
    )
    assert advance(window) == window.end
    next_window = resolve_window(
        column="ts", initial="2026-01-01T00:00:00+00:00", lag_seconds=60,
        previous=advance(window), now=NOW + timedelta(minutes=1),
    )
    assert next_window.start == window.end  # 相邻窗口无重叠、无缝隙


def test_window_payload_is_json_safe() -> None:
    """窗口事实可落 JSON 列（只含治理事实，无业务数据）。"""
    window = resolve_window(
        column="updated_at", initial=None, lag_seconds=120, previous="2026-10-01T00:00:00+00:00",
        now=NOW,
    )
    payload = window.as_payload()
    assert payload == {
        "column": "updated_at",
        "start": "2026-10-01T00:00:00+00:00",
        "end": (NOW - timedelta(seconds=120)).isoformat(),
        "lag_seconds": 120,
        "bounds": "[start,end)",
    }


def test_batch_mode_has_no_window() -> None:
    """BATCH 模式不产生水印窗口（只有增量模式才有窗口语义）。"""
    schedule = PipelineSchedule(
        pipeline_id=uuid.uuid4(),
        cron="0 2 * * *",
        timezone="Asia/Shanghai",
        mode=MODE_BATCH,
        misfire_policy="SKIP",
        misfire_grace_seconds=120,
        watermark_column="updated_at",
        watermark_initial="2026-01-01T00:00:00+00:00",
        watermark_lag_seconds=60,
    )
    assert build_watermark_window(schedule, now=NOW) is None


def test_incremental_mode_builds_window() -> None:
    """WATERMARK_INCREMENTAL 模式按登记契约构造窗口（FR-41）。"""
    schedule = PipelineSchedule(
        pipeline_id=uuid.uuid4(),
        cron="0 2 * * *",
        timezone="Asia/Shanghai",
        mode=MODE_WATERMARK_INCREMENTAL,
        misfire_policy="FIRE_ONCE",
        misfire_grace_seconds=120,
        watermark_column="updated_at",
        watermark_initial="2026-01-01T00:00:00+00:00",
        watermark_lag_seconds=60,
    )
    window = build_watermark_window(schedule, now=NOW)
    assert window is not None
    assert window.column == "updated_at"
    assert window.lag_seconds == 60
    assert window.end == (NOW - timedelta(seconds=60)).isoformat()
