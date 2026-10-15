"""M-J Harness 安全执行内核 — HARNESS-P0-CROSS-PROJECT（跨项目工具意图）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 8 章「安全 · 多租户」
覆盖 FR：FR-29（P0 规则）、FR-66（跨项目意图直接 DENY，无审批通道）
对应 AC：AC-12（跨项目工具意图 → HARNESS-P0-CROSS-PROJECT 直接 DENY）
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from etl_agent.harness.pdp.engine import PdpRequest, RuleOutcome


class CrossProjectRule:
    """意图目标项目与 Actor 所属项目不一致 → P0 / DENY（无放行通道）。"""

    rule_id = "HARNESS-P0-CROSS-PROJECT"
    version = "1"
    risk_level = "P0"

    def evaluate(self, request: PdpRequest, params: Mapping[str, Any]) -> RuleOutcome | None:
        if request.actor_project_id is None:
            return None
        if request.actor_project_id == request.project_id:
            return None
        return RuleOutcome(
            self.rule_id,
            self.risk_level,
            "DENY",
            {"target_project": request.project_id, "actor_project": request.actor_project_id},
        )
