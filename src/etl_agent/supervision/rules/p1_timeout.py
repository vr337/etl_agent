"""M-L 运行时监督 — P1 超时规则（`SUPERVISION-P1-TIMEOUT`）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 8 章「性能 · 运行时预算硬上限」
覆盖 FR：FR-45（P1 超时规则：运行时长 > 冻结预算 → 硬中断）、FR-46（动作 CANCEL）
对应 AC：AC-08（超预算 → 硬中断 / 隔离）

判定：观测时长 > 契约 `max_duration_seconds`（默认 3600s）→ CANCEL / P1。
超时按「不可继续等待」处理：长跑作业占用引擎槽位会拖垮后续调度，宁可中断后重跑。
"""
from __future__ import annotations

from etl_agent.supervision.contracts import (
    RuleMatch,
    SupervisionContract,
    SupervisionObservation,
)
from etl_agent.supervision.mitigation import ACTION_CANCEL

RULE_ID = "SUPERVISION-P1-TIMEOUT"
RISK_LEVEL = "P1"
ACTIONS: tuple[str, ...] = (ACTION_CANCEL,)


def evaluate(
    contract: SupervisionContract, observation: SupervisionObservation
) -> RuleMatch | None:
    """运行时长超预算即命中。"""
    value = observation.duration_seconds
    if value is None or value <= contract.max_duration_seconds:
        return None
    return RuleMatch(
        rule_id=RULE_ID,
        risk_level=RISK_LEVEL,
        actions=ACTIONS,
        observed={"duration_seconds": value},
        threshold={"max_duration_seconds": contract.max_duration_seconds},
        detail=f"运行时长 {value}s 超过冻结预算 {contract.max_duration_seconds}s（P1 硬中断）",
    )


__all__ = ["ACTIONS", "RISK_LEVEL", "RULE_ID", "evaluate"]
