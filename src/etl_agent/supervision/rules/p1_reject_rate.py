"""M-L 运行时监督 — P1 拒绝率规则（`SUPERVISION-P1-REJECT-RATE`）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 8 章「性能 · 运行时预算硬上限」
覆盖 FR：FR-45（P1 拒绝率规则：拒绝率 > 0.5 → 硬中断 + 隔离）、FR-46（动作 CANCEL + QUARANTINE）
对应 AC：AC-08（超预算 → 硬中断 / 隔离）

判定：观测拒绝率 > 契约 `max_reject_rate`（默认 0.5）→ **CANCEL + QUARANTINE**。

两个动作都要执行（提示词 §8.4）：CANCEL 终止作业（止损），QUARANTINE 阻断发布（防止
「质量已判定不合格」的数据被原子 Swap 上正式表）。QUARANTINE 是 S8 与 T-15 的联动红线
（禁止事项 15），因此本条规则是本阶段唯一产出双动作的规则。
"""
from __future__ import annotations

from etl_agent.supervision.contracts import (
    RuleMatch,
    SupervisionContract,
    SupervisionObservation,
)
from etl_agent.supervision.mitigation import ACTION_CANCEL, ACTION_QUARANTINE

RULE_ID = "SUPERVISION-P1-REJECT-RATE"
RISK_LEVEL = "P1"
ACTIONS: tuple[str, ...] = (ACTION_CANCEL, ACTION_QUARANTINE)


def evaluate(
    contract: SupervisionContract, observation: SupervisionObservation
) -> RuleMatch | None:
    """拒绝率超上限即命中（CANCEL + QUARANTINE）。"""
    value = observation.reject_rate
    if value is None or value <= contract.max_reject_rate:
        return None
    return RuleMatch(
        rule_id=RULE_ID,
        risk_level=RISK_LEVEL,
        actions=ACTIONS,
        observed={"reject_rate": value},
        threshold={"max_reject_rate": contract.max_reject_rate},
        detail=f"拒绝率 {value} 超过上限 {contract.max_reject_rate}（P1：硬中断 + 隔离）",
    )


__all__ = ["ACTIONS", "RISK_LEVEL", "RULE_ID", "evaluate"]
