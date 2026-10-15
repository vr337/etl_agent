"""M-J Harness 安全执行内核 — HARNESS-P0-SECRET-EXFIL（Secret 外发）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 8 章「安全 · 凭据保护」
覆盖 FR：FR-29（P0 规则）、FR-06（凭据不外发）
对应 AC：AC-02（无敏感明文泄露）、AC-12（跨边界意图 DENY）

判定：调用方显式声明「Secret 外发意图」（`secret_export`）即 DENY——
受管凭据操作（迁移 / 暂存 / 激活）走注册清单与四眼审批，不属于本规则。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from etl_agent.harness.pdp.engine import PdpRequest, RuleOutcome


class SecretExfilRule:
    """Secret 值外发意图 → P0 / DENY（无人工放行通道）。"""

    rule_id = "HARNESS-P0-SECRET-EXFIL"
    version = "1"
    risk_level = "P0"

    def evaluate(self, request: PdpRequest, params: Mapping[str, Any]) -> RuleOutcome | None:
        if not request.secret_export:
            return None
        return RuleOutcome(
            self.rule_id,
            self.risk_level,
            "DENY",
            {"tool_name": request.tool_name, "reason": "secret_export_intent"},
        )
