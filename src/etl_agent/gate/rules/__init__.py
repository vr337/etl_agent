"""M-G 确定性门禁 — 14 项规则注册表（GATE-001 ~ GATE-014）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-G / 第 3.2 章 M-F（FR-19 清单）
覆盖 FR：FR-19（14 项确定性静态契约校验）
对应 AC：AC-04（不合规候选被拦截并进入有限修复）

注册表是门禁的**唯一规则来源**：门禁服务只遍历本表，不内联任何规则逻辑，
新增规则必须在此登记（SPEC 3.2 M-G 的 14 项与 FR-19 清单逐项对应）。
"""
from __future__ import annotations

from etl_agent.gate.contracts import GateRule
from etl_agent.gate.rules.database_endpoints import DatabaseEndpoints
from etl_agent.gate.rules.hdfs_contract import HdfsContract
from etl_agent.gate.rules.incremental_contract import IncrementalContract
from etl_agent.gate.rules.managed_file_source import ManagedFileSource
from etl_agent.gate.rules.no_plaintext_credentials import NoPlaintextCredentials
from etl_agent.gate.rules.profile_topology import ProfileTopology
from etl_agent.gate.rules.quality_contract import (
    QualityContractPredicates,
    QualityContractRouting,
    QualityContractSink,
    QualityContractTraceability,
)
from etl_agent.gate.rules.row_llm_contract import RowLlmContract
from etl_agent.gate.rules.supported_connectors import SupportedConnectors, SupportedTransforms
from etl_agent.gate.rules.target_schema import TargetSchemaAligned

# 顺序与 SPEC 3.2 M-G / S5 提示词 11.5 的 14 项清单一致（稳定排序，便于逐项核对）
ALL_RULES: tuple[GateRule, ...] = (
    NoPlaintextCredentials(),
    SupportedConnectors(),
    SupportedTransforms(),
    QualityContractSink(),
    QualityContractRouting(),
    QualityContractTraceability(),
    QualityContractPredicates(),
    ProfileTopology(),
    ManagedFileSource(),
    HdfsContract(),
    DatabaseEndpoints(),
    TargetSchemaAligned(),
    IncrementalContract(),
    RowLlmContract(),
)

RULE_IDS: tuple[str, ...] = tuple(rule.rule_id for rule in ALL_RULES)

__all__ = ["ALL_RULES", "RULE_IDS"]
