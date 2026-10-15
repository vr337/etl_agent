"""M-J Harness 安全执行内核 — HARNESS-P0-DESTRUCTIVE-OP（破坏性操作）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 3.2 章 M-L（受管回滚）/ 第 8 章「安全 · 授权与职责分离」
覆盖 FR：FR-29（P0 规则）、FR-37（9 类副作用同协议）、PRD 2.3-3（P0 必须双人批准）
对应 AC：AC-01（P0 场景 required_approvals=2）
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from etl_agent.harness.pdp.engine import PdpRequest, RuleOutcome
from etl_agent.harness.side_effects.registry import is_destructive, is_registered

# 破坏性 DDL 关键字：出现在命令类型 / 工具名中即按破坏性处置
DESTRUCTIVE_KEYWORDS = ("DROP", "TRUNCATE", "DESTROY", "CLEANUP", "ROLLBACK", "CANCEL")


class DestructiveOpRule:
    """破坏性副作用（含 DROP / TRUNCATE 类命令）→ P0 / REQUIRE_APPROVAL（双人批准）。"""

    rule_id = "HARNESS-P0-DESTRUCTIVE-OP"
    version = "1"
    risk_level = "P0"

    def evaluate(self, request: PdpRequest, params: Mapping[str, Any]) -> RuleOutcome | None:
        command = request.command_type or ""
        if not self._destructive(command, request.tool_name):
            return None
        return RuleOutcome(
            self.rule_id,
            self.risk_level,
            "REQUIRE_APPROVAL",
            {
                "command_type": command or None,
                "required_approvals": int(params.get("required_approvals", 2)),
            },
        )

    @staticmethod
    def _destructive(command_type: str, tool_name: str) -> bool:
        """注册清单优先（非 Agent 自报）；未登记命令按关键字保守判定（不轻易放行）。

        保守方向：命令名 / 工具名含 DROP / TRUNCATE / DESTROY / CLEANUP / ROLLBACK / CANCEL
        即按破坏性处置，交四眼审批——未登记命令宁可多问一句，也不直接放行。
        """
        if command_type and is_registered(command_type):
            return is_destructive(command_type)
        haystack = f"{command_type} {tool_name}".upper()
        return any(keyword in haystack for keyword in DESTRUCTIVE_KEYWORDS)
