"""P3 预算预警规则用例（T-16.7，FR-45 / FR-46）。

判定：任一预算水位 ≥ 契约 `warning_ratio`（默认 80%）→ THROTTLE（P3 软动作）。
软动作不终止运行，只提高监控频率；在所有规则中严格度最低。
"""
from __future__ import annotations

from etl_agent.supervision.contracts import (
    SupervisionContract,
    SupervisionObservation,
)
from etl_agent.supervision.mitigation import ACTION_THROTTLE
from etl_agent.supervision.rules import p3_budget_warning


def test_p3_warning_hit() -> None:
    """任一预算水位 ≥ 80% → THROTTLE（P3 软动作）。"""
    contract = SupervisionContract.frozen()
    match = p3_budget_warning.evaluate(
        contract, SupervisionObservation(records=int(contract.max_records * 0.85))
    )
    assert match is not None
    assert match.rule_id == "SUPERVISION-P3-WARNING"
    assert match.actions == (ACTION_THROTTLE,)
    assert match.risk_level == "P3"


def test_p3_warning_below_ratio_not_hit() -> None:
    """水位低于 80% 不命中；None 不命中。"""
    contract = SupervisionContract.frozen()
    assert p3_budget_warning.evaluate(
        contract, SupervisionObservation(records=int(contract.max_records * 0.5))
    ) is None
    assert p3_budget_warning.evaluate(
        contract, SupervisionObservation(records=None)
    ) is None
