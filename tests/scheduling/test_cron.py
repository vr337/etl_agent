"""cron 解析与触发时刻用例（T-14.2 / FR-40）。

覆盖：5 段解析（`*` / 区间 / 列表 / 步长）、日与星期的 OR 语义、时区解析、
正向 / 反向触发时刻、不可达表达式与非法输入的拒绝。
"""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from etl_agent.scheduling.errors import ScheduleValidationError
from etl_agent.scheduling.service import (
    next_fire_time,
    parse_cron,
    previous_fire_time,
    resolve_timezone,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_parse_basic_fields() -> None:
    """`*/5 * * * *` → 每 5 分钟（0,5,...,55）。"""
    expr = parse_cron("*/5 * * * *")
    assert expr.minutes == frozenset(range(0, 60, 5))
    assert len(expr.hours) == 24
    assert expr.day_restricted is False


def test_parse_range_list_and_step() -> None:
    """区间 / 列表 / 区间步长（`0-30/10` → 0,10,20,30）。"""
    expr = parse_cron("0-30/10 9,18 * * 1-5")
    assert expr.minutes == frozenset({0, 10, 20, 30})
    assert expr.hours == frozenset({9, 18})
    assert expr.weekdays == frozenset({1, 2, 3, 4, 5})
    assert expr.weekday_restricted is True


def test_parse_weekday_seven_normalized_to_sunday() -> None:
    """cron 的 7 与 0 同为周日（归一化避免同一槽位判两次）。"""
    assert parse_cron("0 0 * * 7").weekdays == frozenset({0})
    assert parse_cron("0 0 * * 0").weekdays == frozenset({0})


@pytest.mark.parametrize(
    "expression",
    [
        "* * * *",  # 段数不足
        "* * * * * *",  # 段数过多
        "60 * * * *",  # 分钟越界
        "* 24 * * *",  # 小时越界
        "0 0 0 * *",  # 日越界（1-31）
        "0 0 * 13 *",  # 月越界
        "0 0 * * 8",  # 星期越界
        "30-10 * * * *",  # 倒序区间
        "*/0 * * * *",  # 步长为 0
        "MON * * * *",  # 不支持英文别名
    ],
)
def test_parse_rejects_invalid(expression: str) -> None:
    """非法表达式一律拒绝（不静默降级为「永不触发」）。"""
    with pytest.raises(ScheduleValidationError):
        parse_cron(expression)


def test_resolve_timezone() -> None:
    """IANA 时区解析；非法时区拒绝。"""
    assert str(resolve_timezone("Asia/Shanghai")) == "Asia/Shanghai"
    with pytest.raises(ScheduleValidationError):
        resolve_timezone("Mars/Olympus")


def test_next_fire_time_daily() -> None:
    """每日 02:30（Asia/Shanghai）：严格晚于给定时刻。"""
    after = datetime(2026, 10, 15, 1, 0, tzinfo=SHANGHAI)
    result = next_fire_time("30 2 * * *", "Asia/Shanghai", after)
    assert result == datetime(2026, 10, 15, 2, 30, tzinfo=SHANGHAI)

    after_passed = datetime(2026, 10, 15, 3, 0, tzinfo=SHANGHAI)
    assert next_fire_time("30 2 * * *", "Asia/Shanghai", after_passed) == datetime(
        2026, 10, 16, 2, 30, tzinfo=SHANGHAI
    )


def test_next_fire_time_weekday_only() -> None:
    """周一至周五 09:00：周六的请求顺延到周一（日与星期同时受限时取 OR 的标准 cron 语义）。"""
    saturday = datetime(2026, 10, 17, 10, 0, tzinfo=SHANGHAI)  # 2026-10-17 是周六
    result = next_fire_time("0 9 * * 1-5", "Asia/Shanghai", saturday)
    assert result.weekday() == 0  # 周一
    assert result.hour == 9


def test_previous_fire_time_is_latest_slot() -> None:
    """反向查找：`now` 之前最近的槽位（到期扫描不遍历全部历史）。"""
    now = datetime(2026, 10, 15, 10, 7, tzinfo=SHANGHAI)
    result = previous_fire_time("*/5 * * * *", "Asia/Shanghai", now)
    assert result == datetime(2026, 10, 15, 10, 5, tzinfo=SHANGHAI)


def test_unreachable_expression_rejected() -> None:
    """2 月 30 日不可达 → 明确拒绝（不返回「永远不会触发」的静默配置）。"""
    now = datetime(2026, 10, 15, 0, 0, tzinfo=SHANGHAI)
    with pytest.raises(ScheduleValidationError):
        next_fire_time("0 0 30 2 *", "Asia/Shanghai", now)


def test_timezone_conversion_from_utc() -> None:
    """跨时区换算：UTC 输入按目标时区解释（调度语义跟随时区）。"""
    after_utc = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)  # 北京时间 02:00
    result = next_fire_time("0 3 * * *", "Asia/Shanghai", after_utc)
    assert result.astimezone(UTC).hour == 19  # 北京 03:00 = UTC 19:00
