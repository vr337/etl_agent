"""P0 记录预算规则用例（T-16.2，FR-45）。

判定：观测记录数 > 契约 `max_records`（默认 10,000,000）→ CANCEL / P0。
含边界（等于不命中）与观测缺失（None 不命中）。
"""
from __future__ import annotations

from etl_agent.supervision.contracts import (
    SupervisionContract,
    SupervisionObservation,
)
from etl_agent.supervision.mitigation import ACTION_CANCEL
from etl_agent.supervision.rules import p0_record_budget


def test_p0_record_budget_hit() -> None:
    """记录超预算 → CANCEL（P0）。"""
    contract = SupervisionContract.frozen()
    match = p0_record_budget.evaluate(
        contract, SupervisionObservation(records=contract.max_records + 1)
    )
    assert match is not None
    assert match.rule_id == "SUPERVISION-P0-RECORD"
    assert match.actions == (ACTION_CANCEL,)
    assert match.risk_level == "P0"


def test_p0_record_budget_boundary_not_hit() -> None:
    """等于预算不命中（严格大于才命中）；None 不命中。"""
    contract = SupervisionContract.frozen()
    assert p0_record_budget.evaluate(
        contract, SupervisionObservation(records=contract.max_records)
    ) is None
    assert p0_record_budget.evaluate(contract, SupervisionObservation(records=None)) is None
