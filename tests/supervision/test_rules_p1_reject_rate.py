"""P1 拒绝率规则用例（T-16.6，FR-45 / FR-46）。

判定：观测拒绝率 > 契约 `max_reject_rate`（默认 0.5）→ **CANCEL + QUARANTINE**。
这是唯一双动作规则：拒绝率超限既终止运行、又隔离制品（阻断发布与原子 Swap）。
"""
from __future__ import annotations

from etl_agent.supervision.contracts import (
    SupervisionContract,
    SupervisionObservation,
)
from etl_agent.supervision.mitigation import ACTION_CANCEL, ACTION_QUARANTINE
from etl_agent.supervision.rules import p1_reject_rate


def test_p1_reject_rate_hit_dual_action() -> None:
    """拒绝率 > 0.5 → CANCEL + QUARANTINE（唯一双动作规则）。"""
    contract = SupervisionContract.frozen()
    match = p1_reject_rate.evaluate(
        contract, SupervisionObservation(reject_rate=contract.max_reject_rate + 0.1)
    )
    assert match is not None
    assert match.rule_id == "SUPERVISION-P1-REJECT-RATE"
    assert set(match.actions) == {ACTION_CANCEL, ACTION_QUARANTINE}
    assert match.risk_level == "P1"


def test_p1_reject_rate_boundary_not_hit() -> None:
    """等于阈值不命中；None 不命中。"""
    contract = SupervisionContract.frozen()
    assert p1_reject_rate.evaluate(
        contract, SupervisionObservation(reject_rate=contract.max_reject_rate)
    ) is None
    assert p1_reject_rate.evaluate(contract, SupervisionObservation(reject_rate=None)) is None
