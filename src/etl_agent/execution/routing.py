"""M-K 执行与数据面 — 质量分流路由与 QualityReport 生成（FR-44）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-K / 第 5.2 章 quality_reports
覆盖 FR：FR-44（错误分流与质量报告：未命中规则数据写入错误表并记录原始值与违规原因；
QualityReport 含 PASSED/WARNING/FAILED/INCOMPLETE、总/通过/拒绝行数、拒绝率、错误码统计、诊断）
对应 AC：AC-06（有效数据落入影子表、错误数据按质量契约分流写入错误表）

口径（SPEC 2.8.4 例外条款 / PRD 10.5）：**原始值与违规原因只落数据面错误表**
（SeaTunnel 引擎侧写入），报告 / 证据 / 快照只回聚合（总数、拒绝率、错误码计数）。
本模块只做「聚合 → 报告」的确定性计算，不触碰任何业务数据正文。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import QualityReport

# QualityReport 状态（SPEC 5.2 quality_reports.status 注释）
STATUS_PASSED = "PASSED"
STATUS_WARNING = "WARNING"
STATUS_FAILED = "FAILED"
STATUS_INCOMPLETE = "INCOMPLETE"

# 预警比例（FR-45 冻结契约：0.8 触发预警；质量契约超限前 80% 即 WARNING）
WARNING_RATIO = 0.8


@dataclass(frozen=True)
class QualityOutcome:
    """引擎侧回传的质量分流事实（聚合口径，不含业务正文）。

    `total_rows` 为 None 表示引擎未回传计数（不可判定完整度）→ 报告 INCOMPLETE。
    """

    total_rows: int | None = None
    passed_rows: int = 0
    rejected_rows: int = 0
    error_codes: dict[str, int] = field(default_factory=dict)
    valid_sink: str | None = None
    error_sink: str | None = None


def route_outcome(contract: dict[str, Any], outcome: QualityOutcome) -> QualityOutcome:
    """按质量契约校正分流去向（有效 sink / 错误 sink 路由匹配，FR-44）。

    契约未声明 sink 时保留引擎回传值（不臆造表名）；`routing` 规则目前由引擎侧
    匹配执行，控制面只登记去向事实供报告引用。
    """
    valid_sink = contract.get("valid_sink")
    error_sink = contract.get("error_sink")
    return QualityOutcome(
        total_rows=outcome.total_rows,
        passed_rows=outcome.passed_rows,
        rejected_rows=outcome.rejected_rows,
        error_codes=dict(outcome.error_codes),
        valid_sink=str(valid_sink) if valid_sink else outcome.valid_sink,
        error_sink=str(error_sink) if error_sink else outcome.error_sink,
    )


def classify_quality(
    contract: dict[str, Any], outcome: QualityOutcome
) -> tuple[str, float | None]:
    """质量状态判定（FR-44 + FR-45 预警比例）。

    - INCOMPLETE：引擎未回传总行数（无法判定）；
    - FAILED：拒绝行数或拒绝率超出契约阈值（max_rejected_rows / max_reject_rate）；
    - WARNING：达到阈值的 80%（预警比例 0.8，FR-45）；
    - PASSED：其余。
    """
    if outcome.total_rows is None:
        return STATUS_INCOMPLETE, None
    total = outcome.total_rows
    reject_rate = (outcome.rejected_rows / total) if total > 0 else 0.0
    max_rows = contract.get("max_rejected_rows")
    max_rate = contract.get("max_reject_rate")
    over_rows = isinstance(max_rows, int) and outcome.rejected_rows > max_rows
    over_rate = isinstance(max_rate, (int, float)) and reject_rate > float(max_rate)
    if over_rows or over_rate:
        return STATUS_FAILED, reject_rate
    warn_rows = isinstance(max_rows, int) and outcome.rejected_rows >= max_rows * WARNING_RATIO
    warn_rate = (
        isinstance(max_rate, (int, float)) and reject_rate >= float(max_rate) * WARNING_RATIO
    )
    if warn_rows or warn_rate:
        return STATUS_WARNING, reject_rate
    return STATUS_PASSED, reject_rate


async def generate_quality_report(
    session: AsyncSession,
    run_id: uuid.UUID,
    contract: dict[str, Any],
    outcome: QualityOutcome,
) -> QualityReport:
    """生成 / 更新运行的质量报告（幂等：run_id 唯一，重复调用更新同一行）。

    报告字段（SPEC 5.2 quality_reports）：status / 行列数 / reject_rate /
    valid_sink / error_sink / rules（契约规则引用）/ diagnostics（错误码统计等聚合事实）。
    """
    routed = route_outcome(contract, outcome)
    status, reject_rate = classify_quality(contract, routed)
    diagnostics: list[dict[str, Any]] = [
        {"kind": "error_codes", "counts": dict(routed.error_codes)},
    ]
    if status == STATUS_INCOMPLETE:
        diagnostics.append({"kind": "engine_metrics_missing", "detail": "引擎未回传总行数"})
    existing = (
        (
            await session.execute(
                select(QualityReport).where(QualityReport.run_id == run_id)
            )
        )
        .scalars()
        .first()
    )
    payload = {
        "status": status,
        "total_rows": routed.total_rows,
        "passed_rows": routed.passed_rows,
        "rejected_rows": routed.rejected_rows,
        "reject_rate": reject_rate,
        "valid_sink": routed.valid_sink,
        "error_sink": routed.error_sink,
        "rules": _contract_rules(contract),
        "diagnostics": diagnostics,
        "generated_at": datetime.now(UTC),
    }
    if existing is None:
        report = QualityReport(run_id=run_id, **payload)
        session.add(report)
    else:
        for key, value in payload.items():
            setattr(existing, key, value)
        report = existing
    await session.flush()
    return report


def _contract_rules(contract: dict[str, Any]) -> list[dict[str, Any]]:
    """契约规则引用（只引名称与表达式，不回传业务数据；FR-22 质量契约要素）。"""
    rules: list[dict[str, Any]] = []
    for predicate in contract.get("predicates") or []:
        if isinstance(predicate, dict):
            rules.append(
                {
                    "name": str(predicate.get("name", "")),
                    "expression": str(predicate.get("expression", "")),
                }
            )
    return rules
