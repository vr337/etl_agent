"""M-J Harness 安全执行内核 — HARNESS-P2-SCHEMA-MISMATCH（Schema 漂移）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 3.2 章 M-K（同 Schema 强校验）
覆盖 FR：FR-29（P2 规则）、FR-43（原子 Swap 的同 Schema 前置）
对应 AC：AC-01（P2 单审批）

判定：调用方声明目标 Schema 未对齐（`target_schema_aligned=False`）→ P2 / REQUIRE_APPROVAL。
未声明（None）不命中——对齐校验由 M-G 门禁 GATE-012 承担，PDP 只做意图侧裁决。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from etl_agent.harness.pdp.engine import PdpRequest, RuleOutcome


class SchemaMismatchRule:
    """目标 Schema 未对齐 → P2 / REQUIRE_APPROVAL。"""

    rule_id = "HARNESS-P2-SCHEMA-MISMATCH"
    version = "1"
    risk_level = "P2"

    def evaluate(self, request: PdpRequest, params: Mapping[str, Any]) -> RuleOutcome | None:
        if request.target_schema_aligned is not False:
            return None
        return RuleOutcome(
            self.rule_id,
            self.risk_level,
            "REQUIRE_APPROVAL",
            {"target_schema_aligned": False},
        )
