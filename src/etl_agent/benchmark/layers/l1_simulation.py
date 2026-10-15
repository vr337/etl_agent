"""M-M 评测与安全进化 — L1 模拟故障评测（仅在模拟器内注入故障）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 6.2 章 API-15
覆盖 FR：FR-50（L1 故障矩阵 + 能力令牌重复消费幂等拒绝 + 部分成功与未知状态分类）
对应 AC：AC-15（全部故障选中最强减损 + 重复消费被幂等拒绝 + 部分成功/未知状态正确分类）

**只在模拟器内**（S9 禁止事项 20）：故障注入全部在内存态发生，不连引擎、不写数据面。
重复消费复用 `ReplayGuard` 的判定语义（`consumed_at` 非空即拒绝），
故模拟器实现成同语义的内存计数器——真实实现由
`tests/harness/` 下的 ReplayGuard 用例覆盖，本层只验证「被拒绝」这一结果。

**分类口径**（AC-15「未知状态零误判为成功」）：
- `SUCCESS`：明确成功；
- `PARTIAL_SUCCESS`：部分成功（必须走最强减损，不得当成功处理）；
- `UNKNOWN`：未知状态（必须当失败处理，`reject_as_success=False`）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from etl_agent.benchmark.layers.base import BenchmarkSuite, LayerResult, Scenario
from etl_agent.supervision.contracts import RuleMatch
from etl_agent.supervision.mitigation import ACTION_CANCEL, plan_mitigation

# 故障类型（与场景 `fault.kind` 对应）
FAULT_TIMEOUT = "TIMEOUT"
FAULT_NETWORK = "NETWORK"
FAULT_PARTIAL = "PARTIAL_SUCCESS"
FAULT_REPLAY = "REPLAY"
FAULT_UNKNOWN = "UNKNOWN"

# 故障 → 应选中的最强减损动作（AC-15「全部故障选中最强减损动作」）
FAULT_STRICTEST_ACTION: dict[str, str] = {
    FAULT_TIMEOUT: ACTION_CANCEL,
    FAULT_NETWORK: ACTION_CANCEL,
    FAULT_PARTIAL: ACTION_CANCEL,
    FAULT_REPLAY: ACTION_CANCEL,
    FAULT_UNKNOWN: ACTION_CANCEL,
}

# 执行结果分类
CLASS_SUCCESS = "SUCCESS"
CLASS_PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
CLASS_UNKNOWN = "UNKNOWN"
CLASS_FAILED = "FAILED"

# 未知 / 部分成功**不得**被判为成功（AC-15：未知状态零误判为成功）
NON_SUCCESS_CLASSES: frozenset[str] = frozenset(
    {CLASS_PARTIAL_SUCCESS, CLASS_UNKNOWN, CLASS_FAILED}
)


@dataclass
class FaultSimulator:
    """内存态故障模拟器（无外部副作用）。"""

    kind: str
    stage: str = "EXECUTE"
    consumed_tokens: dict[str, int] = field(default_factory=dict)

    def classify(self) -> str:
        """把故障类型映射为执行结果分类。"""
        if self.kind == FAULT_PARTIAL:
            return CLASS_PARTIAL_SUCCESS
        if self.kind in (FAULT_UNKNOWN, FAULT_TIMEOUT, FAULT_NETWORK):
            return CLASS_UNKNOWN
        return CLASS_FAILED

    def consume_token(self, token_id: str) -> bool:
        """模拟令牌消费：首次成功，二次起拒绝（ReplayGuard 同语义）。

        Returns:
            True 表示消费成功；False 表示被幂等拒绝（重复消费）。
        """
        count = self.consumed_tokens.get(token_id, 0)
        self.consumed_tokens[token_id] = count + 1
        return count == 0

    def strictest_mitigation(self) -> str:
        """由故障推导最强减损动作（走真实 `plan_mitigation` 聚合）。"""
        if self.kind not in FAULT_STRICTEST_ACTION:
            return ACTION_CANCEL
        match = RuleMatch(
            rule_id=f"SIM-{self.kind}",
            risk_level="P0",
            actions=(FAULT_STRICTEST_ACTION[self.kind],),
            observed={"kind": self.kind},
            threshold={},
            detail=f"模拟故障 {self.kind}",
        )
        return plan_mitigation([match]).action


class L1SimulationEvaluator:
    """L1 模拟故障评测器。"""

    layer = "L1"

    def evaluate_one(self, scenario: Scenario) -> dict[str, Any]:
        """评估单个 L1 场景：故障矩阵 + 重复消费幂等 + 状态分类。"""
        fault_spec: dict[str, Any] = dict(scenario.payload.get("fault", {}))
        kind = str(fault_spec.get("kind", ""))
        simulator = FaultSimulator(kind=kind, stage=str(fault_spec.get("stage", "EXECUTE")))

        classification = simulator.classify()
        mitigation = simulator.strictest_mitigation()

        # 重复消费幂等（仅 REPLAY 场景校验）
        replay_rejected = True
        if kind == FAULT_REPLAY:
            token_id = f"{scenario.id}-token"
            first = simulator.consume_token(token_id)
            second = simulator.consume_token(token_id)
            replay_rejected = bool(first) and not second
            simulated_mitigation = simulator.strictest_mitigation()

            # 期望 [初次成功且二次被拒] + [故障矩阵里的期望减损动作]
            expected = scenario.payload.get("expected_mitigation")
            mitigation = simulated_mitigation
            return {
                "scenario_id": scenario.id,
                "layer": scenario.layer,
                "priority": scenario.priority,
                "type": scenario.type,
                "name": scenario.name,
                "fault_kind": kind,
                "classification": classification,
                "chosen_mitigation": mitigation,
                "expected_mitigation": expected,
                "replay_rejected": replay_rejected,
                "misclassified_as_success": classification not in NON_SUCCESS_CLASSES,
                "ok": bool(replay_rejected and (expected is None or mitigation == expected)),
            }

        expected = scenario.payload.get("expected_mitigation")
        # 未知 / 部分成功不得判为成功
        misclassified = classification not in NON_SUCCESS_CLASSES
        ok = (expected is None or mitigation == expected) and not misclassified
        return {
            "scenario_id": scenario.id,
            "layer": scenario.layer,
            "priority": scenario.priority,
            "type": scenario.type,
            "name": scenario.name,
            "fault_kind": kind,
            "classification": classification,
            "chosen_mitigation": mitigation,
            "expected_mitigation": expected,
            "replay_rejected": replay_rejected,
            "misclassified_as_success": misclassified,
            "ok": bool(ok),
        }

    async def run(
        self, suite: BenchmarkSuite, scenarios: tuple[Scenario, ...]
    ) -> LayerResult:
        """执行全部 L1 场景（纯内存，无外部副作用）。"""
        result = LayerResult(layer=self.layer)
        for scenario in scenarios:
            detail = self.evaluate_one(scenario)
            result.total += 1
            result.detailed.append(detail)
            if detail["ok"]:
                result.passed += 1
            elif scenario.is_legitimate:
                # L1 场景失败记为「未通过」；合法场景被误处理即误报
                result.false_positives += 1
        return result


__all__ = [
    "CLASS_FAILED",
    "CLASS_PARTIAL_SUCCESS",
    "CLASS_SUCCESS",
    "CLASS_UNKNOWN",
    "FAULT_NETWORK",
    "FAULT_PARTIAL",
    "FAULT_REPLAY",
    "FAULT_STRICTEST_ACTION",
    "FAULT_TIMEOUT",
    "FAULT_UNKNOWN",
    "NON_SUCCESS_CLASSES",
    "FaultSimulator",
    "L1SimulationEvaluator",
]
