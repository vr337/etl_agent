"""回滚保留期与容量预警用例（T-16.17，FR-48）。

覆盖：保留期默认 7 天、容量预警阈值 20、只收窄不放宽、
到期判定、可清理判定（未回滚完毕永不可清理）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from etl_agent.rollback.artifacts import (
    STATUS_AVAILABLE,
    STATUS_CLEANUP_PENDING,
    STATUS_INVALID_METADATA,
    STATUS_ROLLBACK_PENDING,
    STATUS_ROLLED_BACK,
)
from etl_agent.rollback.retention import (
    DEFAULT_CAPACITY_WARN_THRESHOLD,
    DEFAULT_RETENTION_DAYS,
    capacity_warning,
    evaluate_retention,
    expires_at,
    is_cleanable,
    is_expired,
    resolve_capacity_threshold,
    resolve_retention_days,
)

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


@dataclass
class _Rollback:
    """最小回滚事实替身（只带保留期判定需要的字段）。"""

    artifact_status: str = STATUS_ROLLED_BACK
    created_at: datetime | None = None
    artifact_manifest: dict | None = None


def test_retention_7_days_default() -> None:
    """保留期默认 7 天（SPEC 5.2 冻结值）。"""
    assert DEFAULT_RETENTION_DAYS == 7
    assert resolve_retention_days(None) == 7
    assert resolve_retention_days(3) == 3


def test_capacity_warning_threshold_20() -> None:
    """容量预警阈值默认 20（SPEC 5.2 冻结值）。"""
    assert DEFAULT_CAPACITY_WARN_THRESHOLD == 20
    assert resolve_capacity_threshold(None) == 20
    assert resolve_capacity_threshold(5) == 5


def test_resolve_only_narrows_not_widens() -> None:
    """只收窄不放宽：超过默认值的请求回落默认值。"""
    assert resolve_retention_days(30) == DEFAULT_RETENTION_DAYS
    assert resolve_capacity_threshold(100) == DEFAULT_CAPACITY_WARN_THRESHOLD
    # 非正数 / 非法类型回落默认
    assert resolve_retention_days(0) == 1  # max(1, min(0,7))
    assert resolve_retention_days(-5) == 1
    assert resolve_retention_days("7") == DEFAULT_RETENTION_DAYS
    assert resolve_retention_days(True) == DEFAULT_RETENTION_DAYS


def test_capacity_warning_at_threshold() -> None:
    """达到阈值即预警（非严格大于）。"""
    assert capacity_warning(20, 20) is True
    assert capacity_warning(21, 20) is True
    assert capacity_warning(19, 20) is False


def test_expires_at_adds_retention() -> None:
    """到期时刻 = 创建时刻 + 保留期。"""
    assert expires_at(NOW, 7) == NOW + timedelta(days=7)
    assert expires_at(None, 7) is None


def test_is_expired_after_retention() -> None:
    """超过保留期 → expired；未超过 → 未到期。"""
    created = NOW - timedelta(days=8)
    rollback = _Rollback(artifact_manifest={"created_at": created.isoformat(), "retention_days": 7})
    assert is_expired(rollback, now=NOW) is True

    fresh = _Rollback(
        artifact_manifest={"created_at": (NOW - timedelta(days=1)).isoformat(), "retention_days": 7}
    )
    assert is_expired(fresh, now=NOW) is False


def test_is_cleanable_requires_rolled_back() -> None:
    """未回滚完毕的制品永远不可清理（保留期是为了「还能回滚」）。"""
    expired_manifest = {"created_at": (NOW - timedelta(days=30)).isoformat(), "retention_days": 7}
    for status in (STATUS_AVAILABLE, STATUS_ROLLBACK_PENDING):
        assert (
            is_cleanable(
                _Rollback(artifact_status=status, artifact_manifest=expired_manifest), now=NOW
            )
            is False
        )


def test_is_cleanable_rolled_back_and_expired() -> None:
    """已回滚 + 已到期 → 可清理。"""
    expired_manifest = {"created_at": (NOW - timedelta(days=30)).isoformat(), "retention_days": 7}
    for status in (STATUS_ROLLED_BACK, STATUS_CLEANUP_PENDING, STATUS_INVALID_METADATA):
        assert (
            is_cleanable(
                _Rollback(artifact_status=status, artifact_manifest=expired_manifest), now=NOW
            )
            is True
        )


def test_evaluate_retention_counts() -> None:
    """保留期结论统计：总数 / 期内 / 到期 / 预警。"""
    old = {"created_at": (NOW - timedelta(days=30)).isoformat(), "retention_days": 7}
    new = {"created_at": (NOW - timedelta(days=1)).isoformat(), "retention_days": 7}
    rows = [
        _Rollback(artifact_manifest=old),
        _Rollback(artifact_manifest=new),
        _Rollback(artifact_manifest=new),
    ]
    status = evaluate_retention(rows, now=NOW)
    assert status.total == 3
    assert status.expired == 1
    assert status.within_retention == 2
    assert status.threshold == 20
    assert status.retention_days == 7
    assert status.capacity_warning is False
    assert "保留期" in status.summary()


def test_evaluate_retention_triggers_capacity_warning() -> None:
    """制品数达到阈值 → 预警。"""
    new = {"created_at": NOW.isoformat(), "retention_days": 7}
    rows = [_Rollback(artifact_manifest=new) for _ in range(20)]
    status = evaluate_retention(rows, now=NOW)
    assert status.capacity_warning is True
    assert "容量预警" in status.summary()


def test_evaluate_retention_empty() -> None:
    """空集合 → 计数归零（不误报预警）。"""
    status = evaluate_retention([], now=NOW)
    assert status.total == 0 and status.expired == 0
    assert status.capacity_warning is False


def test_expired_falls_back_to_created_at() -> None:
    """清单无 created_at 时回落行 created_at。"""
    rollback = _Rollback(created_at=NOW - timedelta(days=10), artifact_manifest={})
    assert is_expired(rollback, now=NOW) is True


def _unused() -> uuid.UUID:  # pragma: no cover - 保留导入语义
    return uuid.uuid4()
