"""M-G 确定性门禁 — GATE-008 Profile 拓扑。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-G / 第 3.2 章 M-D（端点角色 SOURCE/SINK）/ 第 5.2 章 data_profiles
覆盖 FR：FR-19（Profile 拓扑契约）、FR-12（Profile 端点角色）
对应 AC：AC-04（不合规候选被拦截）、AC-12（画像须属当前项目）

判定：来源 / 目标画像必须存在、角色匹配（SOURCE/SINK）且列清单非空。
S4 吸收 5 的处置：画像缺失与「探查通道不可用」是两件事——后者在 Profile 生成阶段
已由 AMEND-002 的 `BIZ_422_CONNECTOR_UNSUPPORTED` 明确拒绝，故此处缺失一律按拓扑错误拦截。
"""
from __future__ import annotations

from etl_agent.gate.contracts import (
    SEVERITY_ERROR,
    Context,
    Plan,
    RuleResult,
    column_entries,
)


class ProfileTopology:
    """GATE-008：画像存在性 + 端点角色 + 列清单。"""

    rule_id = "GATE-008"
    description = "Profile 拓扑"
    severity = SEVERITY_ERROR

    def check(self, plan: Plan, hocon: str, context: Context) -> RuleResult:
        problems: list[str] = []
        for role, expected in (("source_profile", "SOURCE"), ("sink_profile", "SINK")):
            profile = context.get(role)
            if not isinstance(profile, dict):
                problems.append(f"{role} 缺失")
                continue
            actual_role = str(profile.get("endpoint_role", "")).upper()
            if actual_role != expected:
                problems.append(f"{role} 角色错配（期望 {expected}，实际 {actual_role or '未声明'}）")
            if not column_entries(profile):
                problems.append(f"{role} 列清单为空")
        if problems:
            return RuleResult.of(self, False, "；".join(problems))
        return RuleResult.of(self, True, "来源 SOURCE / 目标 SINK 画像齐备且列清单非空")
