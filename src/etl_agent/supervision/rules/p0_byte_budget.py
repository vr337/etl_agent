"""M-L 运行时监督 — P0 字节预算规则（`SUPERVISION-P0-BYTE`）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 8 章「性能 · 运行时预算硬上限」
覆盖 FR：FR-45（P0 字节预算规则：字节数 > 冻结预算 → 硬中断）、FR-46（动作 CANCEL）
对应 AC：AC-08（超预算 → 硬中断 / 隔离）

判定：观测字节数 > 契约 `max_bytes`（默认 10 GiB）→ CANCEL / P0。
观测缺失不命中（同 P0 记录预算：不把「不知道」当「超预算」）。
"""
from __future__ import annotations

from etl_agent.supervision.contracts import (
    RuleMatch,
    SupervisionContract,
    SupervisionObservation,
)
from etl_agent.supervision.mitigation import ACTION_CANCEL

RULE_ID = "SUPERVISION-P0-BYTE"
RISK_LEVEL = "P0"
ACTIONS: tuple[str, ...] = (ACTION_CANCEL,)


def evaluate(
    contract: SupervisionContract, observation: SupervisionObservation
) -> RuleMatch | None:
    """字节数超预算即命中。"""
    value = observation.bytes_total
    if value is None or value <= contract.max_bytes:
        return None
    return RuleMatch(
        rule_id=RULE_ID,
        risk_level=RISK_LEVEL,
        actions=ACTIONS,
        observed={"bytes": value},
        threshold={"max_bytes": contract.max_bytes},
        detail=f"字节数 {value} 超过冻结预算 {contract.max_bytes}（P0 硬中断）",
    )


__all__ = ["ACTIONS", "RISK_LEVEL", "RULE_ID", "evaluate"]
