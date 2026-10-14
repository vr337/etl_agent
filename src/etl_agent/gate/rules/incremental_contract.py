"""M-G 确定性门禁 — GATE-013 增量契约。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-G / 第 3.2 章 M-K（水印增量契约）/ 第 5.2 章 pipeline_schedules
覆盖 FR：FR-19（增量契约）、FR-23（水印增量契约）、FR-41（水印推进）
对应 AC：AC-04（不合规候选被拦截）

判定：声明增量时，水印列必须真实存在于来源画像且类型族为 TIMESTAMP，滞后秒数非负。
水印列不存在或类型不是时间列 → 水印窗口无法推进，属于「生成即不可执行」的候选。
"""
from __future__ import annotations

from etl_agent.gate.contracts import (
    SEVERITY_ERROR,
    Context,
    Plan,
    RuleResult,
    column_entries,
    type_family,
)


class IncrementalContract:
    """GATE-013：水印列存在性 + 时间类型 + 滞后秒数。"""

    rule_id = "GATE-013"
    description = "增量契约"
    severity = SEVERITY_ERROR

    def check(self, plan: Plan, hocon: str, context: Context) -> RuleResult:
        incremental = plan.get("incremental")
        if incremental is None:
            return RuleResult.of(self, True, "非增量模式，无需水印契约")
        if not isinstance(incremental, dict):
            return RuleResult.of(self, False, "incremental 必须是对象")
        watermark = incremental.get("watermark_column")
        if not watermark:
            return RuleResult.of(self, False, "incremental.watermark_column 缺失")

        source_columns = {
            str(entry.get("name")): entry.get("type") for entry in column_entries(context.get("source_profile"))
        }
        if str(watermark) not in source_columns:
            return RuleResult.of(self, False, f"水印列 {watermark} 不存在于来源画像")
        if type_family(source_columns[str(watermark)]) != "TIMESTAMP":
            return RuleResult.of(
                self, False, f"水印列 {watermark} 类型非 TIMESTAMP（FR-23）"
            )
        lag = incremental.get("lag_seconds")
        if lag is None or not isinstance(lag, int) or lag < 0:
            return RuleResult.of(self, False, "incremental.lag_seconds 必须为非负整数")
        return RuleResult.of(self, True, f"水印列 {watermark} 为时间类型，滞后 {lag}s")
