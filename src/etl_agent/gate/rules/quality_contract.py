"""M-G 确定性门禁 — GATE-004~007 质量契约四要素。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-G / 第 3.2 章 M-K（QualityContract）/ 第 5.2 章 quality_reports
覆盖 FR：FR-19（质量契约 sink / routing / traceability / predicates 四项契约）、FR-22（QualityContract 配置）
对应 AC：AC-04（不合规候选被拦截）、AC-06/07（后续质量分流与报告的前置契约）

判定：四要素缺一即拦——质量契约是数据面分流与错误表写入的唯一依据，
缺要素会让违规数据无出口，属于「生成即不可执行」的候选。
"""
from __future__ import annotations

from typing import Any

from etl_agent.gate.contracts import SEVERITY_ERROR, Context, Plan, RuleResult


def _quality_contract(plan: Plan) -> dict[str, Any] | None:
    contract = plan.get("quality_contract")
    return contract if isinstance(contract, dict) else None


class QualityContractSink:
    """GATE-004：有效 sink / 错误 sink 必须齐备且互异。"""

    rule_id = "GATE-004"
    description = "质量契约 sink 路由"
    severity = SEVERITY_ERROR

    def check(self, plan: Plan, hocon: str, context: Context) -> RuleResult:
        contract = _quality_contract(plan)
        if contract is None:
            return RuleResult.of(self, False, "缺少 quality_contract")
        valid_sink = contract.get("valid_sink")
        error_sink = contract.get("error_sink")
        if not valid_sink or not error_sink:
            return RuleResult.of(self, False, "valid_sink / error_sink 必须同时声明")
        if valid_sink == error_sink:
            return RuleResult.of(self, False, "valid_sink 与 error_sink 不得为同一目标")
        return RuleResult.of(self, True, "有效 / 错误 sink 齐备且互异")


class QualityContractRouting:
    """GATE-005：routing 必须非空且只指向已声明的 sink。"""

    rule_id = "GATE-005"
    description = "质量契约 routing"
    severity = SEVERITY_ERROR

    def check(self, plan: Plan, hocon: str, context: Context) -> RuleResult:
        contract = _quality_contract(plan)
        if contract is None:
            return RuleResult.of(self, False, "缺少 quality_contract")
        routing = contract.get("routing")
        if not isinstance(routing, list) or not routing:
            return RuleResult.of(self, False, "routing 必须为非空数组")
        declared = {contract.get("valid_sink"), contract.get("error_sink")}
        dangling = [
            str(item.get("sink"))
            for item in routing
            if not isinstance(item, dict) or item.get("sink") not in declared
        ]
        if dangling:
            return RuleResult.of(self, False, f"routing 指向未声明的 sink：{', '.join(dangling)}")
        return RuleResult.of(self, True, f"{len(routing)} 条路由均指向已声明的 sink")


class QualityContractTraceability:
    """GATE-006：traceability 必须含来源行标识 / 错误码 / 违规原因三列。"""

    rule_id = "GATE-006"
    description = "质量契约 traceability"
    severity = SEVERITY_ERROR

    required = ("source_row_id_column", "error_code_column", "reason_column")

    def check(self, plan: Plan, hocon: str, context: Context) -> RuleResult:
        contract = _quality_contract(plan)
        if contract is None:
            return RuleResult.of(self, False, "缺少 quality_contract")
        traceability = contract.get("traceability")
        if not isinstance(traceability, dict):
            return RuleResult.of(self, False, "traceability 必须是对象")
        missing = [key for key in self.required if not traceability.get(key)]
        if missing:
            return RuleResult.of(self, False, f"traceability 缺少字段：{', '.join(missing)}")
        return RuleResult.of(self, True, "来源行标识 / 错误码 / 违规原因三列齐备")


class QualityContractPredicates:
    """GATE-007：predicates 必须非空且每条含 name + expression。"""

    rule_id = "GATE-007"
    description = "质量契约 predicates"
    severity = SEVERITY_ERROR

    def check(self, plan: Plan, hocon: str, context: Context) -> RuleResult:
        contract = _quality_contract(plan)
        if contract is None:
            return RuleResult.of(self, False, "缺少 quality_contract")
        predicates = contract.get("predicates")
        if not isinstance(predicates, list) or not predicates:
            return RuleResult.of(self, False, "predicates 必须为非空数组")
        malformed = [
            str(index)
            for index, item in enumerate(predicates)
            if not isinstance(item, dict) or not item.get("name") or not item.get("expression")
        ]
        if malformed:
            return RuleResult.of(self, False, f"predicates 第 {', '.join(malformed)} 项缺少 name / expression")
        return RuleResult.of(self, True, f"{len(predicates)} 条断言齐备")
