"""P1 超时规则用例（T-16.4，FR-45）。

判定：观测时长 > 契约 `max_duration_seconds`（默认 3600s）→ CANCEL / P1。
"""
from __future__ import annotations

from etl_agent.supervision.contracts import (
    SupervisionContract,
    SupervisionObservation,
)
from etl_agent.supervision.mitigation import ACTION_CANCEL
from etl_agent.supervision.rules import p1_timeout


def test_p1_timeout_hit() -> None:
    """时长超预算 → CANCEL（P1）。"""
    contract = SupervisionContract.frozen()
    match = p1_timeout.evaluate(
        contract,
        SupervisionObservation(duration_seconds=contract.max_duration_seconds + 0.1),
    )
    assert match is not None
    assert match.rule_id == "SUPERVISION-P1-TIMEOUT"
    assert match.risk_level == "P1"
    assert match.actions == (ACTION_CANCEL,)


def test_p1_timeout_boundary_not_hit() -> None:
    """等于预算不命中；None 不命中。"""
    contract = SupervisionContract.frozen()
    assert p1_timeout.evaluate(
        contract, SupervisionObservation(duration_seconds=float(contract.max_duration_seconds))
    ) is None
    assert p1_timeout.evaluate(
        contract, SupervisionObservation(duration_seconds=None)
    ) is None
