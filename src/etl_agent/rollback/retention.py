"""M-L 受管回滚 — 保留期与容量预警管理（FR-48）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 5.2 章 rollback_requests
（`retention_days` 默认 7 / `capacity_warn_threshold` 默认 20）
覆盖 FR：FR-48（回滚表保留期默认 7 天、容量预警阈值 20；到期可申请清理）
对应 AC：AC-08（回滚制品按期清理，不无限堆积）

口径：

- **保留期 7 天**：从制品清单创建时刻起 7 天内的回滚制品视为「可回滚」；
  超过 7 天进入 `CLEANUP_PENDING`（待清理申请），由清理链路走 Harness 协议执行；
- **容量预警阈值 20**：保留期内的制品数达到 20 件即预警（`capacity_warning=True`），
  预警**不阻断**任何操作，只在运行中心提示运维该清理了——清理动作本身仍需审批
  （禁止事项 17：不绕过 Harness 协议直接清理）；
- 两个阈值都可被调用方收紧（更短保留期 / 更低阈值）而不允许放宽：
  与监督契约同向的「只收窄不放宽」，避免「配置能把 7 天改成 7 年」。

本模块是**纯函数集合**：只读事实算结论，不写库、不触发副作用。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from etl_agent.rollback.artifacts import (
    STATUS_CLEANUP_PENDING,
    STATUS_INVALID_METADATA,
    STATUS_ROLLED_BACK,
)

# SPEC 5.2 冻结默认值
DEFAULT_RETENTION_DAYS = 7
DEFAULT_CAPACITY_WARN_THRESHOLD = 20


@dataclass(frozen=True)
class RetentionStatus:
    """一组回滚制品的保留期结论（预警不阻断，只提示）。"""

    total: int
    within_retention: int
    expired: int
    capacity_warning: bool
    threshold: int
    retention_days: int

    def summary(self) -> str:
        """运行中心可直接展示的一行摘要。"""
        state = "（已触发容量预警）" if self.capacity_warning else ""
        return (
            f"保留期内 {self.within_retention} / {self.total}，到期 {self.expired} 件，"
            f"保留期 {self.retention_days} 天，预警阈值 {self.threshold}{state}"
        )


def resolve_retention_days(requested: object) -> int:
    """解析保留期（只收窄不放宽：非正数 / 非法值回落默认 7，且不超过默认值）。"""
    if isinstance(requested, bool) or not isinstance(requested, int):
        return DEFAULT_RETENTION_DAYS
    return max(1, min(requested, DEFAULT_RETENTION_DAYS))


def resolve_capacity_threshold(requested: object) -> int:
    """解析容量预警阈值（只收窄不放宽：非正数 / 非法值回落默认 20）。"""
    if isinstance(requested, bool) or not isinstance(requested, int):
        return DEFAULT_CAPACITY_WARN_THRESHOLD
    return max(1, min(requested, DEFAULT_CAPACITY_WARN_THRESHOLD))


def expires_at(created_at: datetime | None, retention_days: int) -> datetime | None:
    """制品到期时刻（清单创建时刻 + 保留期；缺创建时刻返回 None）。"""
    if created_at is None:
        return None
    return created_at + timedelta(days=retention_days)


def is_expired(
    rollback: Any, *, now: datetime | None = None, retention_days: int | None = None
) -> bool:
    """回滚制品是否已过保留期（需确认执行完毕才谈清理）。"""
    moment = now or datetime.now(UTC)
    manifest = getattr(rollback, "artifact_manifest", None)
    created_at = _manifest_created_at(manifest)
    if created_at is None:
        created_at = getattr(rollback, "created_at", None)
    if created_at is None:
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    days = (
        retention_days
        if retention_days is not None
        else resolve_retention_days(_manifest_retention_days(manifest))
    )
    deadline = expires_at(created_at, days)
    return deadline is not None and moment > deadline


def is_cleanable(rollback: Any, *, now: datetime | None = None) -> bool:
    """是否可申请清理：**已回滚完毕**且**已过保留期**。

    未回滚完毕的制品（AVAILABLE / ROLLBACK_PENDING）永远不可清理——
    清理掉就等于剥夺回滚能力（FR-48 的保留期是为了「还能回滚」，不是为了「更快删」）。
    """
    status = getattr(rollback, "artifact_status", None)
    if status not in {STATUS_ROLLED_BACK, STATUS_CLEANUP_PENDING, STATUS_INVALID_METADATA}:
        return False
    return is_expired(rollback, now=now)


def capacity_warning(count: int, threshold: int = DEFAULT_CAPACITY_WARN_THRESHOLD) -> bool:
    """是否触发容量预警（达到阈值即预警，非严格大于）。"""
    return count >= threshold


def evaluate_retention(
    rollbacks: list[Any],
    *,
    now: datetime | None = None,
    threshold: int | None = None,
    retention_days: int | None = None,
) -> RetentionStatus:
    """统计一组回滚制品的保留期结论。"""
    moment = now or datetime.now(UTC)
    resolved_threshold = (
        threshold
        if threshold is not None
        else resolve_capacity_threshold(_manifest_threshold(_first(rollbacks)))
    )
    resolved_days = (
        retention_days
        if retention_days is not None
        else resolve_retention_days(_manifest_retention_days(_first(rollbacks)))
    )
    expired = sum(
        1 for item in rollbacks if is_expired(item, now=moment, retention_days=resolved_days)
    )
    total = len(rollbacks)
    return RetentionStatus(
        total=total,
        within_retention=total - expired,
        expired=expired,
        capacity_warning=capacity_warning(total, resolved_threshold),
        threshold=resolved_threshold,
        retention_days=resolved_days,
    )


def _first(rollbacks: list[Any]) -> Any:
    return rollbacks[0] if rollbacks else None


def _manifest_created_at(manifest: object) -> datetime | None:
    if isinstance(manifest, dict):
        raw = manifest.get("created_at")
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                return None
    return None


def _manifest_retention_days(manifest: object) -> object:
    if isinstance(manifest, dict):
        return manifest.get("retention_days")
    return None


def _manifest_threshold(manifest: object) -> object:
    if isinstance(manifest, dict):
        return manifest.get("capacity_warn_threshold")
    return None


__all__ = [
    "DEFAULT_CAPACITY_WARN_THRESHOLD",
    "DEFAULT_RETENTION_DAYS",
    "RetentionStatus",
    "capacity_warning",
    "evaluate_retention",
    "expires_at",
    "is_cleanable",
    "is_expired",
    "resolve_capacity_threshold",
    "resolve_retention_days",
]
