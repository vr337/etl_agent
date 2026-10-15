"""M-J Harness 安全执行内核 — HARNESS-P2-WRITE-PROD（生产环境写入）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 4.2 章环境差异 / 第 8 章「安全 · 授权与职责分离」
覆盖 FR：FR-29（P2 规则）
对应 AC：AC-01（P2 单审批：生产写入需数据维度审批）

判定：目标环境命中策略包 `environments`（默认 prod）→ P2 / REQUIRE_APPROVAL。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from etl_agent.harness.pdp.engine import PdpRequest, RuleOutcome

DEFAULT_ENVIRONMENTS = ("prod",)


class WriteProdRule:
    """生产环境写入 → P2 / REQUIRE_APPROVAL。"""

    rule_id = "HARNESS-P2-WRITE-PROD"
    version = "1"
    risk_level = "P2"

    def evaluate(self, request: PdpRequest, params: Mapping[str, Any]) -> RuleOutcome | None:
        environments = params.get("environments") or DEFAULT_ENVIRONMENTS
        protected = {str(item).lower() for item in environments}
        if request.environment.lower() not in protected:
            return None
        return RuleOutcome(
            self.rule_id, self.risk_level, "REQUIRE_APPROVAL", {"environment": request.environment}
        )
