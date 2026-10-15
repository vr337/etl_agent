"""P0 输出放大比规则用例（T-16.5，FR-45 / FR-46）。

判定：观测放大比 > 契约 `max_amplification_ratio`（默认 2.0）→ CANCEL / P0。
放大比失控通常来自错误的关联 / 笛卡尔积，比「行数多」更危险。
"""
from __future__ import annotations

from etl_agent.supervision.contracts import (
    SupervisionContract,
    SupervisionObservation,
)
from etl_agent.supervision.mitigation import ACTION_CANCEL
from etl_agent.supervision.rules import p0_amplification


def test_p0_amplification_hit() -> None:
    """放大比 > 2.0 → CANCEL（P0）。"""
    contract = SupervisionContract.frozen()
    match = p0_amplification.evaluate(
        contract, SupervisionObservation(amplification_ratio=contract.max_amplification_ratio + 0.5)
    )
    assert match is not None
    assert match.rule_id == "SUPERVISION-P0-AMPLIFICATION"
    assert match.actions == (ACTION_CANCEL,)
    assert match.risk_level == "P0"


def test_p0_amplification_boundary_not_hit() -> None:
    """等于上限不命中；None 不命中。"""
    contract = SupervisionContract.frozen()
    assert p0_amplification.evaluate(
        contract, SupervisionObservation(amplification_ratio=contract.max_amplification_ratio)
    ) is None
    assert p0_amplification.evaluate(
        contract, SupervisionObservation(amplification_ratio=None)
    ) is None
