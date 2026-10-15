"""misfire 策略用例（T-14.3 / FR-40）。

覆盖：宽限内正常触发、SKIP 跳过、FIRE_ONCE 补跑一次、未到期不触发、宽限可配置。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from etl_agent.scheduling.misfire import (
    ALLOWED_MISFIRE_POLICIES,
    DEFAULT_GRACE_SECONDS,
    POLICY_FIRE_ONCE,
    POLICY_SKIP,
    decide_misfire,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
SLOT = datetime(2026, 10, 15, 2, 0, tzinfo=SHANGHAI)


def test_policies_are_the_two_registered_values() -> None:
    """策略集合与 SPEC 5.2 一致（SKIP / FIRE_ONCE）。"""
    assert ALLOWED_MISFIRE_POLICIES == {"SKIP", "FIRE_ONCE"}
    assert DEFAULT_GRACE_SECONDS == 120


def test_within_grace_fires() -> None:
    """宽限内（Beat 抖动 / 短暂阻塞）正常触发，不算错过。"""
    decision = decide_misfire(
        policy=POLICY_SKIP, scheduled_for=SLOT, now=SLOT + timedelta(seconds=30)
    )
    assert decision.fire is True
    assert decision.reason == "within-grace"


def test_misfire_skip() -> None:
    """超过宽限且策略 SKIP → 不补跑。"""
    decision = decide_misfire(
        policy=POLICY_SKIP, scheduled_for=SLOT, now=SLOT + timedelta(minutes=30)
    )
    assert decision.fire is False
    assert decision.reason == "misfire-skip"
    assert decision.late_seconds == 1800


def test_misfire_fire_once() -> None:
    """超过宽限且策略 FIRE_ONCE → 补跑一次（只补最近一个槽位，不按错过数展开）。"""
    decision = decide_misfire(
        policy=POLICY_FIRE_ONCE, scheduled_for=SLOT, now=SLOT + timedelta(hours=6)
    )
    assert decision.fire is True
    assert decision.reason == "misfire-fire-once"


def test_not_due_does_not_fire() -> None:
    """未到期不触发（防御性：扫描器本不该走到这里）。"""
    decision = decide_misfire(
        policy=POLICY_FIRE_ONCE, scheduled_for=SLOT, now=SLOT - timedelta(seconds=1)
    )
    assert decision.fire is False
    assert decision.reason == "not-due"


def test_grace_window_is_configurable() -> None:
    """宽限可配置：把宽限放到 1 小时，同一延迟就从「错过」变成「正常触发」。"""
    late = SLOT + timedelta(minutes=30)
    assert decide_misfire(policy=POLICY_SKIP, scheduled_for=SLOT, now=late).fire is False
    assert (
        decide_misfire(
            policy=POLICY_SKIP, scheduled_for=SLOT, now=late, grace_seconds=3600
        ).fire
        is True
    )


def test_boundary_exactly_at_grace_fires() -> None:
    """恰好等于宽限 → 视为正常触发（边界归入容忍侧，避免边界抖动误判错过）。"""
    decision = decide_misfire(
        policy=POLICY_SKIP, scheduled_for=SLOT, now=SLOT + timedelta(seconds=120)
    )
    assert decision.fire is True
    assert decision.reason == "within-grace"
