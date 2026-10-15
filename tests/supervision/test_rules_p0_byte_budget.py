"""P0 字节预算规则用例（T-16.3，FR-45）。

判定：观测字节数 > 契约 `max_bytes`（默认 10 GiB）→ CANCEL / P0。
"""
from __future__ import annotations

from etl_agent.supervision.contracts import (
    SupervisionContract,
    SupervisionObservation,
)
from etl_agent.supervision.mitigation import ACTION_CANCEL
from etl_agent.supervision.rules import p0_byte_budget


def test_p0_byte_budget_hit() -> None:
    """字节超预算 → CANCEL（P0）。"""
    contract = SupervisionContract.frozen()
    match = p0_byte_budget.evaluate(
        contract, SupervisionObservation(bytes_total=contract.max_bytes + 1)
    )
    assert match is not None
    assert match.rule_id == "SUPERVISION-P0-BYTE"
    assert match.actions == (ACTION_CANCEL,)
    assert match.risk_level == "P0"


def test_p0_byte_budget_boundary_not_hit() -> None:
    """等于预算不命中；None 不命中。"""
    contract = SupervisionContract.frozen()
    assert p0_byte_budget.evaluate(
        contract, SupervisionObservation(bytes_total=contract.max_bytes)
    ) is None
    assert p0_byte_budget.evaluate(contract, SupervisionObservation(bytes_total=None)) is None
