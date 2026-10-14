"""M-G 确定性门禁 — GATE-012 目标 Schema 对齐。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-G / 第 3.2 章 M-K（同 Schema 强校验）/ 第 3.2 章 M-H
覆盖 FR：FR-19（目标 Schema 对齐）、FR-43（原子 Swap 的同 Schema 前置）
对应 AC：AC-04（不合规候选被拦截）

判定：候选声明的目标列必须与目标画像列**同集合**，且类型族一致——
这是 FR-43「同 Schema 强校验」的前置条件，列名或类型漂移会让原子 Swap 直接失败。
"""
from __future__ import annotations

from etl_agent.gate.contracts import (
    SEVERITY_ERROR,
    Context,
    Plan,
    RuleResult,
    column_entries,
    type_family,
)


class TargetSchemaAligned:
    """GATE-012：目标列名 / 类型与目标画像对齐。"""

    rule_id = "GATE-012"
    description = "目标 Schema 对齐"
    severity = SEVERITY_ERROR

    def check(self, plan: Plan, hocon: str, context: Context) -> RuleResult:
        sink = plan.get("sink")
        target_schema = (sink or {}).get("target_schema")
        columns = target_schema.get("columns") if isinstance(target_schema, dict) else None
        if not isinstance(columns, list) or not columns:
            return RuleResult.of(self, False, "sink.target_schema.columns 必须为非空数组")

        declared = {str(item.get("name")): item.get("type") for item in columns if isinstance(item, dict)}
        profile_columns = {str(entry.get("name")): entry.get("type") for entry in column_entries(context.get("sink_profile"))}
        if not profile_columns:
            return RuleResult.of(self, False, "目标画像列清单为空，无法对齐")

        missing = sorted(set(profile_columns) - set(declared))
        extra = sorted(set(declared) - set(profile_columns))
        if missing or extra:
            parts = []
            if missing:
                parts.append(f"缺列 {', '.join(missing)}")
            if extra:
                parts.append(f"多列 {', '.join(extra)}")
            return RuleResult.of(self, False, "；".join(parts))

        mismatched = [
            name
            for name, declared_type in declared.items()
            if type_family(declared_type) != type_family(profile_columns[name])
        ]
        if mismatched:
            return RuleResult.of(self, False, f"类型不一致：{', '.join(sorted(mismatched))}")
        return RuleResult.of(self, True, f"{len(declared)} 列列名与类型族均对齐")
