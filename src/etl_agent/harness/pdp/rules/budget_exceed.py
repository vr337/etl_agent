"""M-J Harness 安全执行内核 — HARNESS-P1-BUDGET-EXCEED（预算超限）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 3.2 章 M-L（冻结预算）/ 第 8 章「性能 · 运行时预算硬上限」
覆盖 FR：FR-29（P1 规则）、FR-45（预算硬上限：记录 10M / 字节 10 GiB / 时长 3600s）
对应 AC：AC-01（P1 单审批）

判定：请求声明的预算超过策略包阈值 → P1 / REQUIRE_APPROVAL。
预算为**不可扩权**上限：PDP 只能拦，不能在裁决里放宽（SPEC 8「运行时预算硬上限」行）。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from etl_agent.harness.pdp.engine import PdpRequest, RuleOutcome

DEFAULT_LIMITS: dict[str, int] = {
    "max_records": 10_000_000,
    "max_bytes": 10 * 1024**3,
    "max_duration_seconds": 3600,
}


class BudgetExceedRule:
    """预算超限 → P1 / REQUIRE_APPROVAL（附超限字段，不含业务数据）。"""

    rule_id = "HARNESS-P1-BUDGET-EXCEED"
    version = "1"
    risk_level = "P1"

    def evaluate(self, request: PdpRequest, params: Mapping[str, Any]) -> RuleOutcome | None:
        budget = request.budget
        if not budget:
            return None
        exceeded: dict[str, Any] = {}
        for key, default_limit in DEFAULT_LIMITS.items():
            limit = params.get(key, default_limit)
            value = budget.get(key)
            if isinstance(limit, int) and isinstance(value, int) and value > limit:
                exceeded[key] = {"requested": value, "limit": limit}
        if not exceeded:
            return None
        return RuleOutcome(self.rule_id, self.risk_level, "REQUIRE_APPROVAL", {"exceeded": exceeded})
