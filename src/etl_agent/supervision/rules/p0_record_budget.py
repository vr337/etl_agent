"""M-L 运行时监督 — P0 记录预算规则（`SUPERVISION-P0-RECORD`）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 8 章「性能 · 运行时预算硬上限」
覆盖 FR：FR-45（P0 记录预算规则：记录数 > 冻结预算 → 硬中断）、FR-46（动作 CANCEL）
对应 AC：AC-08（超预算 → 硬中断）

判定：观测记录数 > 契约 `max_records`（默认 10,000,000）→ CANCEL / P0。
观测缺失（引擎未回传计数）**不命中**——「不知道」不等于「超预算」，
把缺失当命中会让每一次指标抖动都变成一次硬中断。
"""
from __future__ import annotations

from etl_agent.supervision.contracts import (
    RuleMatch,
    SupervisionContract,
    SupervisionObservation,
)
from etl_agent.supervision.mitigation import ACTION_CANCEL

RULE_ID = "SUPERVISION-P0-RECORD"
RISK_LEVEL = "P0"
ACTIONS: tuple[str, ...] = (ACTION_CANCEL,)


def evaluate(
    contract: SupervisionContract, observation: SupervisionObservation
) -> RuleMatch | None:
    """记录数超预算即命中。"""
    records = observation.records
    if records is None or records <= contract.max_records:
        return None
    return RuleMatch(
        rule_id=RULE_ID,
        risk_level=RISK_LEVEL,
        actions=ACTIONS,
        observed={"records": records},
        threshold={"max_records": contract.max_records},
        detail=f"记录数 {records} 超过冻结预算 {contract.max_records}（P0 硬中断）",
    )


__all__ = ["ACTIONS", "RISK_LEVEL", "RULE_ID", "evaluate"]
