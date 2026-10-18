"""M-G 确定性门禁 — GATE-002 受支持连接器契约 / GATE-003 受支持转换契约。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-G / 第 5.2 章 data_connections（connector_type 枚举）
覆盖 FR：FR-19（受支持连接器契约、受支持转换契约）、FR-24（逐行 LLM Transform）
对应 AC：AC-04（不合规候选被拦截）

判定：
- GATE-002：来源端 / 目标端 connector_type 必须落在受支持连接器白名单内
  （与 SPEC 5.2 `data_connections.connector_type` 枚举一致，ADR-07 字符串化；
  基线 8 类 + S11 T-21 追加的 CLICKHOUSE，见 AMEND-011）；
- GATE-003：`transforms[].type` 必须落在转换白名单内，白名单外一律拦截，
  避免生成任意转换语义。
"""
from __future__ import annotations

from etl_agent.gate.contracts import SEVERITY_ERROR, Context, Plan, RuleResult

SUPPORTED_CONNECTORS = frozenset(
    {
        "MYSQL",
        "POSTGRESQL",
        "ORACLE",
        "SQL_SERVER",
        "DORIS",
        "HIVE",
        "HDFS",
        "LOCAL_FILE",
        "CLICKHOUSE",
    }
)

SUPPORTED_TRANSFORMS = frozenset(
    {"sql", "filter", "field_mapper", "replace", "split", "copy", "select", "row_llm"}
)


class SupportedConnectors:
    """GATE-002：连接器类型必须在白名单内。"""

    rule_id = "GATE-002"
    description = "受支持连接器契约"
    severity = SEVERITY_ERROR

    def check(self, plan: Plan, hocon: str, context: Context) -> RuleResult:
        unknown: list[str] = []
        for role in ("source", "sink"):
            block = plan.get(role)
            connector = block.get("connector_type") if isinstance(block, dict) else None
            if connector is None:
                unknown.append(f"{role}=<缺失>")
            elif str(connector).upper() not in SUPPORTED_CONNECTORS:
                unknown.append(f"{role}={connector}")
        if unknown:
            return RuleResult.of(
                self, False, f"不支持的连接器：{', '.join(unknown)}（白名单 {len(SUPPORTED_CONNECTORS)} 类）"
            )
        return RuleResult.of(self, True, "来源端与目标端连接器均在白名单内")


class SupportedTransforms:
    """GATE-003：转换类型必须在白名单内。"""

    rule_id = "GATE-003"
    description = "受支持转换契约"
    severity = SEVERITY_ERROR

    def check(self, plan: Plan, hocon: str, context: Context) -> RuleResult:
        transforms = plan.get("transforms")
        if transforms is None:
            return RuleResult.of(self, True, "未声明转换（纯搬运）")
        if not isinstance(transforms, list):
            return RuleResult.of(self, False, "transforms 必须是数组")
        unknown = [
            str(item.get("type"))
            for item in transforms
            if not isinstance(item, dict)
            or str(item.get("type", "")).lower() not in SUPPORTED_TRANSFORMS
        ]
        if unknown:
            return RuleResult.of(self, False, f"不支持的转换类型：{', '.join(unknown)}")
        return RuleResult.of(self, True, f"{len(transforms)} 个转换均在白名单内")
