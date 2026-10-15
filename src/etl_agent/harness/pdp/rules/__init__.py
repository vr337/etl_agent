"""M-J Harness 安全执行内核 — PDP 9 条规则注册表。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J（PDP 9 条确定性规则）
覆盖 FR：FR-29（9 条规则全注册 + 聚合）
对应 AC：AC-01（P0 双人批准）、AC-12（跨项目 DENY）

注册表是 PDP 的唯一规则来源；规则参数（阈值 / 白名单）一律取自签名策略包。
"""
from __future__ import annotations

from etl_agent.harness.pdp.engine import PdpRule
from etl_agent.harness.pdp.rules.budget_exceed import BudgetExceedRule
from etl_agent.harness.pdp.rules.cross_project import CrossProjectRule
from etl_agent.harness.pdp.rules.destructive_op import DestructiveOpRule
from etl_agent.harness.pdp.rules.plaintext_credential import PlaintextCredentialRule
from etl_agent.harness.pdp.rules.routine import RoutineRule
from etl_agent.harness.pdp.rules.schema_mismatch import SchemaMismatchRule
from etl_agent.harness.pdp.rules.secret_exfil import SecretExfilRule
from etl_agent.harness.pdp.rules.sensitive_export import SensitiveExportRule
from etl_agent.harness.pdp.rules.write_prod import WriteProdRule

# 顺序固定为 P0→P3（聚合与证据顺序稳定，便于审计比对）
ALL_RULES: tuple[PdpRule, ...] = (
    CrossProjectRule(),
    PlaintextCredentialRule(),
    DestructiveOpRule(),
    SecretExfilRule(),
    SensitiveExportRule(),
    BudgetExceedRule(),
    WriteProdRule(),
    SchemaMismatchRule(),
    RoutineRule(),
)

RULE_IDS: tuple[str, ...] = tuple(rule.rule_id for rule in ALL_RULES)

__all__ = ["ALL_RULES", "RULE_IDS"]
