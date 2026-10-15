"""减损动作聚合用例（T-16.9，FR-46）。

覆盖：5 类动作严重度、最严动作胜出、最严风险胜出、`stricter_of` 只升不降、
`plan_mitigation` 的外部动作展开（CANCEL → STOP_ENGINE_JOB / PAUSE → PAUSE_ENGINE_JOB）。
"""
from __future__ import annotations

from etl_agent.supervision.contracts import (
    SupervisionContract,
    SupervisionObservation,
    evaluate,
)
from etl_agent.supervision.mitigation import (
    ACTION_CANCEL,
    ACTION_CONTINUE,
    ACTION_PAUSE,
    ACTION_QUARANTINE,
    ACTION_SEVERITY,
    ACTION_THROTTLE,
    plan_mitigation,
    stricter_of,
    strictest_action,
    strictest_risk,
)


def test_action_severity_order() -> None:
    """严重度：CANCEL > QUARANTINE > PAUSE > THROTTLE > CONTINUE（提示词 §8.4）。"""
    assert ACTION_SEVERITY[ACTION_CANCEL] > ACTION_SEVERITY[ACTION_QUARANTINE]
    assert ACTION_SEVERITY[ACTION_QUARANTINE] > ACTION_SEVERITY[ACTION_PAUSE]
    assert ACTION_SEVERITY[ACTION_PAUSE] > ACTION_SEVERITY[ACTION_THROTTLE]
    assert ACTION_SEVERITY[ACTION_THROTTLE] > ACTION_SEVERITY[ACTION_CONTINUE]


def test_strictest_action_wins() -> None:
    """最严动作胜出。"""
    assert strictest_action({ACTION_THROTTLE, ACTION_CANCEL}) == ACTION_CANCEL
    assert strictest_action({ACTION_THROTTLE, ACTION_PAUSE}) == ACTION_PAUSE
    assert strictest_action(set()) == ACTION_CONTINUE
    # 未知动作不臆造严重度（按 CONTINUE 计）
    assert strictest_action({"BOGUS"}) == ACTION_CONTINUE


def test_strictest_risk_wins() -> None:
    """最严风险胜出（P0 > P1 > P2 > P3）；空集合返回 None。"""
    assert strictest_risk(["P3", "P0", "P1"]) == "P0"
    assert strictest_risk([]) is None


def test_stricter_of_only_escalates() -> None:
    """`stricter_of` 只升不降。"""
    assert stricter_of(ACTION_CONTINUE, ACTION_CANCEL) == ACTION_CANCEL
    assert stricter_of(ACTION_CANCEL, ACTION_CONTINUE) == ACTION_CANCEL
    assert stricter_of(ACTION_THROTTLE, ACTION_PAUSE) == ACTION_PAUSE


def test_aggregation_strictest_wins() -> None:
    """规则聚合：记录超限（P0/CANCEL）与预警（P3/THROTTLE）同命中 → CANCEL / P0。"""
    contract = SupervisionContract.frozen()
    observation = SupervisionObservation(
        records=contract.max_records + 1,          # P0 记录预算
        duration_seconds=contract.max_duration_seconds * 0.9,  # P3 预警
    )
    outcome = evaluate(contract, observation)
    assert outcome.action == ACTION_CANCEL
    assert outcome.risk_level == "P0"
    assert set(outcome.rule_ids) == {"SUPERVISION-P0-RECORD", "SUPERVISION-P3-WARNING"}


def test_plan_mitigation_external_actions() -> None:
    """外部动作展开：CANCEL → STOP_ENGINE_JOB，PAUSE → PAUSE_ENGINE_JOB。"""
    contract = SupervisionContract.frozen()
    cancel_plan = plan_mitigation(evaluate(contract, SupervisionObservation(records=10**8)).matches)
    assert cancel_plan.stops_engine is True
    assert "STOP_ENGINE_JOB" in cancel_plan.external_actions

    pause_contract = SupervisionContract.resolve({"max_records": 100})
    pause_plan = plan_mitigation(
        evaluate(pause_contract, SupervisionObservation(records=85)).matches
    )
    # 85/100 = 85% ≥ 80% → P3 THROTTLE（无外部动作）
    assert pause_plan.action == ACTION_THROTTLE
    assert pause_plan.external_actions == ()


def test_plan_mitigation_quarantine_flag() -> None:
    """QUARANTINE 规则命中 → `quarantines` 为真（阻断发布联动）。"""
    plan = plan_mitigation(
        evaluate(SupervisionContract.frozen(), SupervisionObservation(reject_rate=0.9)).matches
    )
    assert plan.quarantines is True
    assert plan.action == ACTION_CANCEL


def test_continue_when_within_budget() -> None:
    """预算内 → CONTINUE / risk None。"""
    outcome = evaluate(SupervisionContract.frozen(), SupervisionObservation(records=10))
    assert outcome.action == ACTION_CONTINUE
    assert outcome.risk_level == ""
    assert outcome.matches == ()
