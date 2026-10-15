"""M-J Harness 安全执行内核 — HARNESS-P0-PLAINTEXT-CREDENTIAL（HOCON 含明文凭据）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 8 章「安全 · 凭据保护」
覆盖 FR：FR-29（P0 规则）、FR-06（凭据不明文落盘）
对应 AC：AC-02（无敏感明文泄露）

检测口径与 GATE-001 共用 `gate.rules.no_plaintext_credentials.first_plaintext_credential`：
两处各写一份正则迟早漂移，而这是同一条 AC-02 的判定依据。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from etl_agent.gate.rules.no_plaintext_credentials import first_plaintext_credential
from etl_agent.harness.pdp.engine import PdpRequest, RuleOutcome


class PlaintextCredentialRule:
    """候选 HOCON 中出现明文凭据字面量 → P0 / DENY。"""

    rule_id = "HARNESS-P0-PLAINTEXT-CREDENTIAL"
    version = "1"
    risk_level = "P0"

    def evaluate(self, request: PdpRequest, params: Mapping[str, Any]) -> RuleOutcome | None:
        if not request.hocon:
            return None
        leaked_key = first_plaintext_credential(request.hocon)
        if leaked_key is None:
            return None
        # 只记字段名，不记值（AC-02）
        return RuleOutcome(self.rule_id, self.risk_level, "DENY", {"credential_field": leaked_key})
