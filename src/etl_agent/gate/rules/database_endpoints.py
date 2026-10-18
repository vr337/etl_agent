"""M-G 确定性门禁 — GATE-011 数据库端点。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-G / 第 3.2 章 M-C（连接登记）/ 第 5.2 章 data_connections
覆盖 FR：FR-19（数据库端点契约）、FR-04（连接登记）
对应 AC：AC-04（不合规候选被拦截）、AC-12（连接须属当前项目）

判定：数据库类端点的 `connection_id` 必须存在且属当前项目——
跨项目连接视同不存在（沿用 S3/S4「不泄露存在性」语义）。
候选只引用 connection_id，凭据在 Worker 侧物化（FR-68：图内只传占位符）。
"""
from __future__ import annotations

from etl_agent.gate.contracts import SEVERITY_ERROR, Context, Plan, RuleResult

DATABASE_CONNECTORS = frozenset(
    {"MYSQL", "POSTGRESQL", "ORACLE", "SQL_SERVER", "DORIS", "HIVE", "CLICKHOUSE"}
)


class DatabaseEndpoints:
    """GATE-011：数据库端点必须引用当前项目的连接。"""

    rule_id = "GATE-011"
    description = "数据库端点"
    severity = SEVERITY_ERROR

    def check(self, plan: Plan, hocon: str, context: Context) -> RuleResult:
        allowed = {str(item) for item in context.get("connection_ids", [])}
        problems: list[str] = []
        for role in ("source", "sink"):
            block = plan.get(role)
            connector = str((block or {}).get("connector_type", "")).upper()
            if connector not in DATABASE_CONNECTORS:
                continue
            connection_id = (block or {}).get("connection_id")
            if not connection_id:
                problems.append(f"{role} 缺少 connection_id")
            elif str(connection_id) not in allowed:
                problems.append(f"{role}.connection_id 不属于当前项目（跨项目视同不存在）")
        if problems:
            return RuleResult.of(self, False, "；".join(problems))
        return RuleResult.of(self, True, "数据库端点均引用当前项目内连接")
