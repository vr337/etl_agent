"""M-G 确定性门禁 — GATE-009 受管文件源。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-G / 第 3.2 章 M-E（文件资产）/ 第 5.2 章 file_assets
覆盖 FR：FR-19（受管文件源契约）、FR-13（文件资产）
对应 AC：AC-04（不合规候选被拦截）、AC-12（文件资产须属当前项目）

判定：来源端为文件时，`file_source.file_asset_id` 必须存在且属当前项目。
S4 吸收 2 的处置：**不引入对外路径字段**——候选只引用 asset_id，
真实挂载路径（`/data/file-assets`）由引擎侧只读挂载，不进候选、不进响应。
"""
from __future__ import annotations

from etl_agent.gate.contracts import SEVERITY_ERROR, Context, Plan, RuleResult

FILE_CONNECTORS = frozenset({"LOCAL_FILE"})


class ManagedFileSource:
    """GATE-009：文件源必须引用当前项目的受管文件资产。"""

    rule_id = "GATE-009"
    description = "受管文件源"
    severity = SEVERITY_ERROR

    def check(self, plan: Plan, hocon: str, context: Context) -> RuleResult:
        source = plan.get("source")
        connector = str((source or {}).get("connector_type", "")).upper()
        if connector not in FILE_CONNECTORS:
            return RuleResult.of(self, True, "来源端非文件源，无需文件资产引用")
        file_source = plan.get("file_source")
        asset_id = file_source.get("file_asset_id") if isinstance(file_source, dict) else None
        if not asset_id:
            return RuleResult.of(self, False, "文件源缺少 file_source.file_asset_id")
        allowed = {str(item) for item in context.get("file_asset_ids", [])}
        if str(asset_id) not in allowed:
            return RuleResult.of(self, False, "file_asset_id 不属于当前项目（跨项目视同不存在）")
        return RuleResult.of(self, True, "文件源引用的受管资产属当前项目")
