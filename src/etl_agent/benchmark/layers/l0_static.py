"""M-M 评测与安全进化 — L0 静态注入评测（无外部副作用）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 6.2 章 API-15
覆盖 FR：FR-50（L0 静态注入：策略攻击矩阵 + 未选中连接器注入拒绝 + 明文 Secret 制品拒绝）
对应 AC：AC-14（全部攻击被拦 + 零误拦 + 零外部副作用）

**零外部副作用**（S9 禁止事项 20）：本层只用进程内 PDP 纯计算（无 IO、无 LLM、不连数据面），
CI 可重复跑，不产生任何网络或存储写入——这正是「静态注入」的定义。

评测逻辑：对每个场景构造 `PdpRequest` 喂给真实 PDP（`PdpEngine` + 已验签策略包），
按场景声明的 `expected_outcome` / `expected_risk` 断言；P0/P1 场景未达预期即「漏拦」。
"""
from __future__ import annotations

from typing import Any

from etl_agent.benchmark.layers.base import BenchmarkSuite, LayerResult, Scenario
from etl_agent.harness.pdp.engine import PdpEngine, PdpRequest


def _build_request(scenario: Scenario, project_id: str) -> PdpRequest:
    """由场景载荷构造 PDP 请求（只填治理事实，不含凭据）。"""
    spec: dict[str, Any] = dict(scenario.payload.get("pdp_request", {}))
    actor_project = spec.pop("actor_project_id", None)
    # 默认 actor 在同一项目：不声明 actor_project_id 表示「项目内」
    resolved_actor = actor_project if actor_project is not None else project_id
    resource_ids = tuple(spec.pop("resource_ids", ()) or ())
    budget = spec.pop("budget", None)
    classifications = tuple(spec.pop("classifications", ()) or ())
    return PdpRequest(
        project_id=project_id,
        actor_project_id=resolved_actor,
        resource_ids=resource_ids,
        budget=budget,
        classifications=classifications,
        **{key: value for key, value in spec.items() if key in _REQUEST_FIELDS},
    )


# 允许透传的 PdpRequest 字段（白名单，防止场景文件注入意外字段）
_REQUEST_FIELDS: frozenset[str] = frozenset(
    {
        "environment",
        "tool_name",
        "tool_version",
        "pipeline_version_id",
        "artifact_sha256",
        "command_type",
        "side_effect_type",
        "hocon",
        "target_schema_aligned",
        "secret_export",
    }
)


class L0StaticEvaluator:
    """L0 静态注入评测器（进程内 PDP，无 IO）。"""

    layer = "L0"

    def __init__(self, engine: PdpEngine, *, project_id: str) -> None:
        self._engine = engine
        self._project_id = project_id

    def evaluate_one(self, scenario: Scenario) -> dict[str, Any]:
        """评估单个场景，返回判定明细（供门禁聚合）。"""
        request = _build_request(scenario, self._project_id)
        result = self._engine.evaluate(request)
        expected_outcome = scenario.payload.get("expected_outcome")
        expected_risk = scenario.payload.get("expected_risk")
        expected_rule = scenario.payload.get("expected_rule")

        # 拦截判定：场景期望 DENY / REQUIRE_APPROVAL 即属「应被拦」
        should_block = expected_outcome in ("DENY", "REQUIRE_APPROVAL")
        got_block = result.outcome in ("DENY", "REQUIRE_APPROVAL")

        outcome_ok = expected_outcome is None or result.outcome == expected_outcome
        risk_ok = expected_risk is None or result.risk_level == expected_risk
        # 命中规则核对：只对声明了 expected_rule 的场景校验（避免过约束）
        matched_rules = {item["rule_id"] for item in result.rule_evidence}
        rule_ok = expected_rule is None or expected_rule in matched_rules
        # 审批票数核对（P0 破坏性操作 = 2 票）
        expected_approvals = scenario.payload.get("expected_approvals")
        approvals_ok = (
            expected_approvals is None or result.required_approvals >= int(expected_approvals)
        )

        blocked_correctly = (not should_block) or got_block
        return {
            "scenario_id": scenario.id,
            "layer": scenario.layer,
            "priority": scenario.priority,
            "type": scenario.type,
            "name": scenario.name,
            "expected_outcome": expected_outcome,
            "actual_outcome": result.outcome,
            "expected_risk": expected_risk,
            "actual_risk": result.risk_level,
            "should_block": should_block,
            "blocked": got_block,
            "blocked_correctly": blocked_correctly,
            "ok": bool(outcome_ok and risk_ok and rule_ok and approvals_ok),
        }

    async def run(
        self, suite: BenchmarkSuite, scenarios: tuple[Scenario, ...]
    ) -> LayerResult:
        """执行全部 L0 场景（同步计算，接口保持 async 以统一三层调用形态）。"""
        result = LayerResult(layer=self.layer)
        for scenario in scenarios:
            detail = self.evaluate_one(scenario)
            result.total += 1
            result.detailed.append(detail)
            if detail["should_block"] and detail["blocked"]:
                result.blocked += 1
            if not detail["should_block"] and not detail["blocked"]:
                result.passed += 1
            if not detail["should_block"] and detail["blocked"]:
                # 误报：合法场景被拦
                result.false_positives += 1
        return result


__all__ = ["L0StaticEvaluator"]
