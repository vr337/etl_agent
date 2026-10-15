"""M-J Harness 安全执行内核 — HARNESS-P1-SENSITIVE-EXPORT（敏感数据外发）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 3.2 章 M-D（6 级分级）/ 第 8 章「安全 · 授权与职责分离」
覆盖 FR：FR-29（P1 规则）、FR-15（数据分级）、FR-52（受控发布）
对应 AC：AC-01（P1 单审批）、AC-02（分级报告参与裁决）

判定：涉及 RESTRICTED / SECRET 级数据的意图 → P1 / REQUIRE_APPROVAL（安全维度单审批）。
敏感级别集合来自策略包参数（`sensitive_levels`），不硬编码在规则里。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from etl_agent.harness.pdp.engine import PdpRequest, RuleOutcome

DEFAULT_SENSITIVE_LEVELS = ("RESTRICTED", "SECRET")


class SensitiveExportRule:
    """涉及高敏感分级数据的意图 → P1 / REQUIRE_APPROVAL。"""

    rule_id = "HARNESS-P1-SENSITIVE-EXPORT"
    version = "1"
    risk_level = "P1"

    def evaluate(self, request: PdpRequest, params: Mapping[str, Any]) -> RuleOutcome | None:
        levels = params.get("sensitive_levels") or DEFAULT_SENSITIVE_LEVELS
        sensitive = {str(item).upper() for item in levels}
        hit = sorted({item.upper() for item in request.classifications} & sensitive)
        if not hit:
            return None
        return RuleOutcome(
            self.rule_id, self.risk_level, "REQUIRE_APPROVAL", {"classification_levels": hit}
        )
