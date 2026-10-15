"""M-L 运行时监督 — P0 输出放大比规则（`SUPERVISION-P0-AMPLIFICATION`）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 8 章「性能 · 运行时预算硬上限」
覆盖 FR：FR-45（P0 输出放大规则：输出放大比 > 2.0 → 硬中断）、FR-46（动作 CANCEL）
对应 AC：AC-08（超预算 → 硬中断 / 隔离）

判定：观测放大比 > 契约 `max_amplification_ratio`（默认 2.0）→ CANCEL / P0。
放大比 = 输出字节 / 输入字节：写入被放大意味着目标端可能被打爆，
这是比「行数多」更危险的形态（行数可控，放大比失控通常来自错误的关联 / 笛卡尔积）。
"""
from __future__ import annotations

from etl_agent.supervision.contracts import (
    RuleMatch,
    SupervisionContract,
    SupervisionObservation,
)
from etl_agent.supervision.mitigation import ACTION_CANCEL

RULE_ID = "SUPERVISION-P0-AMPLIFICATION"
RISK_LEVEL = "P0"
ACTIONS: tuple[str, ...] = (ACTION_CANCEL,)


def evaluate(
    contract: SupervisionContract, observation: SupervisionObservation
) -> RuleMatch | None:
    """输出放大比超上限即命中。"""
    value = observation.amplification_ratio
    if value is None or value <= contract.max_amplification_ratio:
        return None
    return RuleMatch(
        rule_id=RULE_ID,
        risk_level=RISK_LEVEL,
        actions=ACTIONS,
        observed={"amplification_ratio": value},
        threshold={"max_amplification_ratio": contract.max_amplification_ratio},
        detail=f"输出放大比 {value} 超过上限 {contract.max_amplification_ratio}（P0 硬中断）",
    )


__all__ = ["ACTIONS", "RISK_LEVEL", "RULE_ID", "evaluate"]
