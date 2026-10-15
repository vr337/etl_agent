"""S9 Benchmark — L1 模拟故障评测用例（AC-15）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 6.2 章 API-15
覆盖 FR：FR-50（L1 故障矩阵 + 能力令牌重复消费幂等拒绝 + 部分成功与未知状态分类）
对应 AC：AC-15（全部故障选中最强减损 + 重复消费被幂等拒绝 + 部分成功/未知状态正确分类）
"""

from __future__ import annotations

from etl_agent.benchmark.layers.base import BenchmarkSuite, Scenario
from etl_agent.benchmark.layers.l1_simulation import (
    CLASS_FAILED,
    CLASS_PARTIAL_SUCCESS,
    CLASS_UNKNOWN,
    FAULT_NETWORK,
    FAULT_PARTIAL,
    FAULT_REPLAY,
    FAULT_STRICTEST_ACTION,
    FAULT_TIMEOUT,
    FAULT_UNKNOWN,
    NON_SUCCESS_CLASSES,
    FaultSimulator,
    L1SimulationEvaluator,
)
from etl_agent.supervision.mitigation import ACTION_CANCEL


async def test_l1_all_scenarios_ok(suite: BenchmarkSuite) -> None:
    """**AC-15**：L1 全部 3 场景判定通过。"""
    evaluator = L1SimulationEvaluator()
    result = await evaluator.run(suite, suite.by_layer("L1"))
    assert result.total == 3
    for item in result.detailed:
        assert item["ok"], f"{item['scenario_id']} 判定失败: {item}"
    assert result.passed == 3


async def test_l1_timeout_fault_selects_strictest_mitigation(suite: BenchmarkSuite) -> None:
    """**AC-15**：网络超时 → 选中最强减损动作（CANCEL）。"""
    scenario = _find(suite, "S9-L1-002")
    evaluator = L1SimulationEvaluator()
    detail = evaluator.evaluate_one(scenario)
    assert detail["chosen_mitigation"] == ACTION_CANCEL
    assert detail["classification"] in NON_SUCCESS_CLASSES
    assert detail["ok"] is True


async def test_l1_replay_idempotently_rejected(suite: BenchmarkSuite) -> None:
    """**AC-15**：令牌重复消费 → 二次被幂等拒绝（ReplayGuard 同语义）。"""
    scenario = _find(suite, "S9-L1-003")
    evaluator = L1SimulationEvaluator()
    detail = evaluator.evaluate_one(scenario)
    assert detail["replay_rejected"] is True
    assert detail["ok"] is True


async def test_l1_replay_token_consumed_once() -> None:
    """模拟器令牌消费语义：首次成功、二次起拒绝。"""
    sim = FaultSimulator(kind=FAULT_REPLAY)
    assert sim.consume_token("t-1") is True
    assert sim.consume_token("t-1") is False
    assert sim.consume_token("t-1") is False
    # 不同令牌不受影响
    assert sim.consume_token("t-2") is True


async def test_l1_fault_matrix_all_yield_strictest() -> None:
    """**AC-15**：故障矩阵中**每种**故障都选中最强减损（无豁免项）。"""
    for kind in (FAULT_TIMEOUT, FAULT_NETWORK, FAULT_PARTIAL, FAULT_REPLAY, FAULT_UNKNOWN):
        sim = FaultSimulator(kind=kind)
        assert sim.strictest_mitigation() == FAULT_STRICTEST_ACTION[kind]
        assert sim.strictest_mitigation() == ACTION_CANCEL


async def test_l1_unknown_and_partial_not_success() -> None:
    """**AC-15**：未知 / 部分成功**不得**被误判为成功。"""
    assert FaultSimulator(kind=FAULT_UNKNOWN).classify() == CLASS_UNKNOWN
    assert FaultSimulator(kind=FAULT_TIMEOUT).classify() == CLASS_UNKNOWN
    assert FaultSimulator(kind=FAULT_NETWORK).classify() == CLASS_UNKNOWN
    assert FaultSimulator(kind=FAULT_PARTIAL).classify() == CLASS_PARTIAL_SUCCESS
    assert FaultSimulator(kind=FAULT_REPLAY).classify() == CLASS_FAILED
    for kind in (FAULT_UNKNOWN, FAULT_PARTIAL, FAULT_REPLAY):
        assert FaultSimulator(kind=kind).classify() in NON_SUCCESS_CLASSES


async def test_l1_unknown_fault_flagged_not_success() -> None:
    """未声明的故障类型不得被判为成功（防御性：默认非成功）。"""
    sim = FaultSimulator(kind="SOMETHING_NEW")
    assert sim.classify() == CLASS_FAILED
    assert sim.classify() in NON_SUCCESS_CLASSES


async def test_l1_no_external_side_effects() -> None:
    """**禁止事项 20**：L1 为纯内存模拟——同故障跑两次结果一致。"""
    first = FaultSimulator(kind=FAULT_TIMEOUT).classify()
    second = FaultSimulator(kind=FAULT_TIMEOUT).classify()
    assert first == second


async def test_l1_legitimate_scenario_not_false_positive(suite: BenchmarkSuite) -> None:
    """合法场景（S9-L1-001）不被记为误报。"""
    evaluator = L1SimulationEvaluator()
    result = await evaluator.run(suite, suite.by_layer("L1"))
    assert result.false_positives == 0


def _find(suite: BenchmarkSuite, scenario_id: str) -> Scenario:
    for item in suite.scenarios:
        if item.id == scenario_id:
            return item
    raise AssertionError(f"场景不存在: {scenario_id}")
