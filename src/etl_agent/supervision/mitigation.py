"""M-L 运行时监督 — 减损动作语义与聚合（FR-46）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 8 章「性能 · 运行时预算硬上限」
覆盖 FR：FR-46（减损动作 CONTINUE/THROTTLE/PAUSE/CANCEL/QUARANTINE；QUARANTINE 阻断质量发布与原子交换）
对应 AC：AC-08（超预算 → 硬中断 + 隔离；隔离后不得发布）

| 动作 | 效果 | 外部动作 |
|------|------|---------|
| CONTINUE | 预算内继续 | 无 |
| THROTTLE | 提高监控频率（软动作） | 无（内部动作） |
| PAUSE | 暂停引擎作业（可恢复） | `PAUSE_ENGINE_JOB` |
| CANCEL | 硬中断（终止引擎作业） | `STOP_ENGINE_JOB` |
| QUARANTINE | 隔离：阻断质量发布与原子 Swap | 无（内部事实） |

**聚合规则**：最严动作胜出（CANCEL > QUARANTINE > PAUSE > THROTTLE > CONTINUE）、
最严风险胜出（P0 > P1 > P2 > P3）。

**「只能收窄不能扩权」的实现**：`stricter_of()` 保证监督动作在同一个运行上只升不降——
一旦某轮评估把动作抬到 CANCEL/QUARANTINE，后续轮次即使指标回落也不会被自动降回 CONTINUE
（解除隔离与恢复执行是人工裁决，不是自动回退）。这既满足 SPEC 8 的「不扩权」，
也避免「指标抖动 → 动作反复横跳」造成下游状态不稳。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from etl_agent.supervision.contracts import RuleMatch

ACTION_CONTINUE = "CONTINUE"
ACTION_THROTTLE = "THROTTLE"
ACTION_PAUSE = "PAUSE"
ACTION_QUARANTINE = "QUARANTINE"
ACTION_CANCEL = "CANCEL"

ALL_ACTIONS: tuple[str, ...] = (
    ACTION_CONTINUE,
    ACTION_THROTTLE,
    ACTION_PAUSE,
    ACTION_QUARANTINE,
    ACTION_CANCEL,
)

# 严重度（值越大越严；CANCEL 最严，其次 QUARANTINE）
ACTION_SEVERITY: dict[str, int] = {
    ACTION_CONTINUE: 0,
    ACTION_THROTTLE: 1,
    ACTION_PAUSE: 2,
    ACTION_QUARANTINE: 3,
    ACTION_CANCEL: 4,
}

RISK_SEVERITY: dict[str, int] = {"P3": 0, "P2": 1, "P1": 2, "P0": 3}

# 外部减损动作（需要引擎 / 外部系统配合的动作；其余为内部动作）
EXTERNAL_ACTION_BY_ACTION: dict[str, str] = {
    ACTION_CANCEL: "STOP_ENGINE_JOB",
    ACTION_PAUSE: "PAUSE_ENGINE_JOB",
}

# 需要写入隔离事实的动作（阻断后续发布）
QUARANTINE_ACTIONS: frozenset[str] = frozenset({ACTION_QUARANTINE})


@dataclass(frozen=True)
class MitigationPlan:
    """聚合后的减损方案（动作集合 + 最严风险 + 外部动作清单）。"""

    action: str
    risk_level: str | None
    actions: frozenset[str]
    external_actions: tuple[str, ...]

    @property
    def quarantines(self) -> bool:
        """是否需要写隔离事实（阻断发布与原子 Swap）。"""
        return bool(self.actions & QUARANTINE_ACTIONS)

    @property
    def stops_engine(self) -> bool:
        return ACTION_CANCEL in self.actions


def strictest_action(actions: Iterable[object]) -> str:
    """取最严动作（空集合 → CONTINUE；未知动作按 CONTINUE 处理，不臆造严重度）。"""
    best = ACTION_CONTINUE
    for action in actions:
        if ACTION_SEVERITY.get(str(action), 0) > ACTION_SEVERITY[best]:
            best = str(action)
    return best


def strictest_risk(levels: Iterable[object]) -> str | None:
    """取最严风险等级（空集合 → None，表示「未命中任何规则」而非「P3」）。"""
    best: str | None = None
    for level in levels:
        text = str(level)
        if text not in RISK_SEVERITY:
            continue
        if best is None or RISK_SEVERITY[text] > RISK_SEVERITY[best]:
            best = text
    return best


def stricter_of(current: str, candidate: str) -> str:
    """取两者中更严的动作（保证监督动作在同一运行上只升不降）。"""
    if ACTION_SEVERITY.get(candidate, 0) > ACTION_SEVERITY.get(current, 0):
        return candidate
    return current


def plan_mitigation(matches: Iterable[object]) -> MitigationPlan:
    """把规则命中聚合成减损方案（提示词 §8.4 聚合规则）。"""
    collected: set[str] = set()
    levels: list[str] = []
    for match in matches:
        if isinstance(match, RuleMatch):
            collected.update(match.actions)
            levels.append(match.risk_level)
    action = strictest_action(collected)
    external = tuple(
        sorted(
            EXTERNAL_ACTION_BY_ACTION[item]
            for item in collected
            if item in EXTERNAL_ACTION_BY_ACTION
        )
    )
    return MitigationPlan(
        action=action,
        risk_level=strictest_risk(levels),
        actions=frozenset(collected),
        external_actions=external,
    )


__all__ = [
    "ACTION_CANCEL",
    "ACTION_CONTINUE",
    "ACTION_PAUSE",
    "ACTION_QUARANTINE",
    "ACTION_SEVERITY",
    "ACTION_THROTTLE",
    "ALL_ACTIONS",
    "EXTERNAL_ACTION_BY_ACTION",
    "QUARANTINE_ACTIONS",
    "RISK_SEVERITY",
    "MitigationPlan",
    "plan_mitigation",
    "stricter_of",
    "strictest_action",
    "strictest_risk",
]
