"""M-J Harness 安全执行内核 — HARNESS-P3-ROUTINE（常规操作兜底）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 8 章「性能 · 非 LLM 决策路径」
覆盖 FR：FR-29（P3 规则：常规操作 ALLOW 且无需审批）
对应 AC：AC-05（ALLOW 路径无需审批槽位）

定位：**兜底规则**——任何意图只要未被其他规则命中，即按常规操作放行（P3 / ALLOW）。
设计为恒定命中，使「无规则命中」不可能发生；引擎仍保留防御性兜底（PDP engine）。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from etl_agent.harness.pdp.engine import PdpRequest, RuleOutcome


class RoutineRule:
    """常规操作 → P3 / ALLOW（无审批槽位）。"""

    rule_id = "HARNESS-P3-ROUTINE"
    version = "1"
    risk_level = "P3"

    def evaluate(self, request: PdpRequest, params: Mapping[str, Any]) -> RuleOutcome | None:
        return RuleOutcome(self.rule_id, self.risk_level, "ALLOW", {"tool_name": request.tool_name})
