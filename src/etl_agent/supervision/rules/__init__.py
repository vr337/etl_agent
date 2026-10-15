"""M-L 运行时监督 — 6 条确定性规则注册表。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 5.2 章 runtime_supervision_snapshots
覆盖 FR：FR-45（6 条确定性规则：P0 记录 / P0 字节 / P1 超时 / P0 放大 / P1 拒绝率 / P3 预警）
对应 AC：AC-08（规则命中 → 对应减损动作）

注册表是**唯一装配点**：新增 / 下线规则只需改 `ALL_RULES`（与 S5 门禁规则注册表同构），
`contracts.evaluate()` 通过延迟导入取用，避免「规则模块依赖契约类型、契约模块依赖规则注册表」的循环。
"""
from __future__ import annotations

from typing import Protocol, cast

from etl_agent.supervision.contracts import (
    RuleMatch,
    SupervisionContract,
    SupervisionObservation,
)
from etl_agent.supervision.rules import (
    p0_amplification,
    p0_byte_budget,
    p0_record_budget,
    p1_reject_rate,
    p1_timeout,
    p3_budget_warning,
)


class SupervisionRule(Protocol):
    """监督规则协议（模块级 `evaluate` + 三个元数据常量）。"""

    RULE_ID: str
    RISK_LEVEL: str
    ACTIONS: tuple[str, ...]

    def evaluate(
        self, contract: SupervisionContract, observation: SupervisionObservation
    ) -> RuleMatch | None: ...


# 6 条规则（顺序与 SPEC 5.2 / 提示词 §8.4 表格一致）
# 各规则以「模块」形态实现 Protocol（模块级 evaluate + 元数据常量），
# `cast` 是必要的：模块对象的结构满足 Protocol，但类型系统无法静态证明。
ALL_RULES: tuple[SupervisionRule, ...] = (
    cast("SupervisionRule", p0_record_budget),
    cast("SupervisionRule", p0_byte_budget),
    cast("SupervisionRule", p1_timeout),
    cast("SupervisionRule", p0_amplification),
    cast("SupervisionRule", p1_reject_rate),
    cast("SupervisionRule", p3_budget_warning),
)

# 注册表内的规则 ID（与 contracts.RULE_IDS 必须一致，由测试断言）
REGISTERED_RULE_IDS: tuple[str, ...] = tuple(str(rule.RULE_ID) for rule in ALL_RULES)

__all__ = ["ALL_RULES", "REGISTERED_RULE_IDS", "SupervisionRule"]
