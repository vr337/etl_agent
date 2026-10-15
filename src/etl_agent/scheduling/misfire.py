"""M-K/T-14 调度 — misfire 策略（SKIP / FIRE_ONCE，宽限默认 120s，FR-40）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-K / 第 5.2 章 pipeline_schedules.misfire_policy
覆盖 FR：FR-40（misfire 策略 SKIP/FIRE_ONCE，宽限默认 120s）
对应 AC：AC-06（调度不因「错过」而静默堆叠触发）

判定口径（三态，宽限是「不算错过」的容忍窗）：

| 条件 | 结论 | 说明 |
|------|------|------|
| `now < scheduled_for` | 未到期（`not-due`） | 扫描器不该走到这里，防御性返回 |
| `0 ≤ now - scheduled_for ≤ grace` | 正常触发（`within-grace`） | Beat 抖动 / 短暂阻塞不算错过 |
| `now - scheduled_for > grace` 且 `SKIP` | 跳过（`misfire-skip`） | 不补跑，等下一个正常槽位 |
| `now - scheduled_for > grace` 且 `FIRE_ONCE` | 补跑一次（`misfire-fire-once`） | 只补**最近一个**错过槽位，不按错过数展开 |

FIRE_ONCE 的「一次」由**幂等键**兜底：补跑请求以「调度 ID + 槽位时刻」为幂等键，
同一槽位重复扫描只会得到同一张准备单（FR-31 幂等语义），不会堆叠出多次执行。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

POLICY_SKIP = "SKIP"
POLICY_FIRE_ONCE = "FIRE_ONCE"

ALLOWED_MISFIRE_POLICIES: frozenset[str] = frozenset({POLICY_SKIP, POLICY_FIRE_ONCE})

# 宽限默认 120s（SPEC 5.2 pipeline_schedules.misfire_grace_seconds / FR-40）
DEFAULT_GRACE_SECONDS = 120


@dataclass(frozen=True)
class MisfireDecision:
    """单次到期槽位的触发裁决（fire=False 时 reason 说明为何不触发）。"""

    fire: bool
    policy: str
    scheduled_for: datetime
    late_seconds: float
    reason: str


def decide_misfire(
    *,
    policy: str,
    scheduled_for: datetime,
    now: datetime,
    grace_seconds: int = DEFAULT_GRACE_SECONDS,
) -> MisfireDecision:
    """判定某个到期槽位是否触发（FR-40）。

    Args:
        policy: `SKIP` 或 `FIRE_ONCE`。
        scheduled_for: 该槽位的计划触发时刻（带时区）。
        now: 当前时刻（带时区；测试可注入）。
        grace_seconds: 宽限窗（秒），默认 120。

    Returns:
        MisfireDecision: 触发结论与原因（原因写入扫描摘要，可观测不落业务事实）。
    """
    late = (now - scheduled_for).total_seconds()
    if late < 0:
        return MisfireDecision(False, policy, scheduled_for, late, "not-due")
    if late <= grace_seconds:
        return MisfireDecision(True, policy, scheduled_for, late, "within-grace")
    if policy == POLICY_FIRE_ONCE:
        return MisfireDecision(True, policy, scheduled_for, late, "misfire-fire-once")
    return MisfireDecision(False, policy, scheduled_for, late, "misfire-skip")


__all__ = [
    "ALLOWED_MISFIRE_POLICIES",
    "DEFAULT_GRACE_SECONDS",
    "POLICY_FIRE_ONCE",
    "POLICY_SKIP",
    "MisfireDecision",
    "decide_misfire",
]
