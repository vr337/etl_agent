"""M-G 确定性门禁 — 门禁服务与有限修复预算。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-G / 第 3.2 章 M-F（FR-21）/ 第 8 章「性能 · 非 LLM 决策路径 P95 ≤20ms」
覆盖 FR：FR-19（14 项契约 + 引擎检查聚合判定）、FR-20（引擎检查摘要脱敏）、FR-21（候选预算 ≤3，含初始）
对应 AC：AC-04（不合规候选被拦截并进入有限修复；耗尽确定性失败）

三条红线：
1. 门禁裁决**不用 LLM**（M-G 边界）：只遍历确定性规则，保证非 LLM 决策 P95 ≤20ms；
2. 修复反馈**只传规则 ID + 脱敏摘要**，不传完整 HOCON（11.7，防 prompt 膨胀）；
3. 候选预算**含初始候选**且硬上限 3（FR-21），越界在配置层即被拒绝启动。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from etl_agent.config import LLM_MAX_CANDIDATES_HARD_CAP, Settings
from etl_agent.gate.contracts import GateResult, GateRule, RuleResult, all_rules
from etl_agent.gate.engine_check import EngineChecker, LocalStubEngineChecker

# FR-21 硬上限：初始候选 + 最多 2 次修复（S5 禁止事项 15）
HARD_CAP_CANDIDATES = LLM_MAX_CANDIDATES_HARD_CAP


@dataclass(frozen=True)
class RepairBudget:
    """有限修复预算（含初始候选）。"""

    max_candidates: int

    def allows_next(self, generated_count: int) -> bool:
        """已生成 `generated_count` 份候选时，是否还允许再生成一份。"""
        return generated_count < self.max_candidates

    def exhausted(self, generated_count: int) -> bool:
        return not self.allows_next(generated_count)

    def repair_rounds(self) -> int:
        """允许的修复轮数（不含初始候选）。"""
        return max(self.max_candidates - 1, 0)


def resolve_budget(settings: Settings) -> RepairBudget:
    """从配置解析预算，并**再夹一次硬上限**（配置层已校验，此处防御性收敛）。"""
    return RepairBudget(max_candidates=min(settings.llm_max_candidates, HARD_CAP_CANDIDATES))


async def run_gate(
    plan: Mapping[str, Any],
    hocon: str,
    context: Mapping[str, Any],
    *,
    engine_checker: EngineChecker | None = None,
    rules: Sequence[GateRule] | None = None,
) -> GateResult:
    """执行 14 项静态契约 + 引擎检查，聚合为门禁总判定（FR-19/20）。

    Args:
        plan: 候选 EtlPlan（结构化字典）。
        hocon: 候选 HOCON 正文（只在本进程内使用，不落业务库，FR-67）。
        context: 校验上下文（画像 / 当前项目连接与文件资产清单）。
        engine_checker: 引擎检查器；缺省用本地 stub（真实 SeaTunnel `--check` 留 S7）。
        rules: 规则序列；缺省用注册表 14 项。

    Returns:
        GateResult: 全通过条件 = 无 ERROR 失败 且 引擎检查未失败。
    """
    results = [rule.check(plan, hocon, context) for rule in (rules or all_rules())]
    checker = engine_checker or LocalStubEngineChecker()
    engine = await checker.check(hocon)
    return GateResult(results=results, engine=engine)


def build_repair_feedback(result: GateResult) -> str:
    """构造修复反馈：只含失败规则的 ID / 描述 / 脱敏摘要（11.7）。

    禁止把完整 HOCON 反馈给 LLM——既防 prompt 膨胀，也避免候选正文进入 Provider 请求日志。
    """
    lines = [rule.failure_line() for rule in result.failures]
    if result.engine is not None and not result.engine.ok:
        lines.append(f"ENGINE[{result.engine.checker}] {result.engine.summary}")
    return "\n".join(lines) if lines else ""


def failed_rule_ids(result: GateResult) -> list[str]:
    """失败规则 ID 列表（遥测只记 ID，不记摘要正文）。"""
    return [rule.rule_id for rule in result.failures]


def summarize_rules(results: Sequence[RuleResult]) -> dict[str, Any]:
    """规则结果的非敏感统计（供遥测使用：只有计数与 ID）。"""
    return {
        "rule_count": len(results),
        "passed_count": sum(1 for item in results if item.passed),
        "failed_rule_ids": [item.rule_id for item in results if not item.passed],
    }
