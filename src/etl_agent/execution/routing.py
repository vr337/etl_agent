"""M-K 质量与发布 — 质量分流路由与 QualityReport 生成（FR-44，S8 完整化）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-K / 第 5.2 章 quality_reports / 第 8 章「性能 · 运行时预算硬上限」
覆盖 FR：FR-44（错误分流与质量报告：未命中规则数据写入错误表并记录原始值与违规原因；
QualityReport 含 PASSED/WARNING/FAILED/INCOMPLETE、总/通过/拒绝行数、拒绝率、错误码统计、诊断）、
FR-45（预警比例 0.8 与最低质量行数 100）、FR-46（隔离强制 FAILED）
对应 AC：AC-06（有效数据落入影子表、错误数据按质量契约分流写入错误表）、
AC-07（质量报告 PASSED 是原子发布的前置门禁）、AC-08（隔离后不得判 PASSED）

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

# 最低质量行数（SPEC 5.2 冻结预算：样本不足 100 行不足以判「通过」，按 INCOMPLETE 处理）
DEFAULT_MIN_QUALITY_ROWS = 100


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
    contract: dict[str, Any],
    outcome: QualityOutcome,
    *,
    min_quality_rows: int | None = None,
    quarantined: bool = False,
) -> tuple[str, float | None]:
    """质量状态判定（FR-44 + FR-45 预警比例 + FR-46 隔离阻断）。

    判定顺序即优先级：

    1. **QUARANTINE** → FAILED：隔离是监督动作的判决（数据已被判不可信），
       即使总行数与拒绝率都达标也不得判 PASSED（S8 提示词 §8.8）；
    2. 引擎未回传总行数 → INCOMPLETE（判定不了，不臆造通过）；
    3. 总行数低于**最低质量行数**（默认 100）→ INCOMPLETE：样本量不足以支撑
       「质量通过」的结论，按「判不了」处理而不是「通过」；
    4. 拒绝行数 / 拒绝率超阈值 → FAILED；
    5. 达到阈值 80%（预警比例 0.8，FR-45）→ WARNING；
    6. 其余 → PASSED。

    Args:
        contract: QualityContract（`max_rejected_rows` / `max_reject_rate` / `min_quality_rows`）。
        outcome: 引擎回传的分流聚合事实。
        min_quality_rows: 最低质量行数；缺省取契约值，再缺省 100（SPEC 5.2 冻结预算）。
        quarantined: 运行是否已被隔离（由监督域判定后传入）。
    """
    if quarantined:
        return STATUS_FAILED, (
            (outcome.rejected_rows / outcome.total_rows) if outcome.total_rows else None
        )
    if outcome.total_rows is None:
        return STATUS_INCOMPLETE, None
    total = outcome.total_rows
    threshold_rows = (
        min_quality_rows
        if min_quality_rows is not None
        else int(contract.get("min_quality_rows") or DEFAULT_MIN_QUALITY_ROWS)
    )
    if total < threshold_rows:
        return STATUS_INCOMPLETE, (outcome.rejected_rows / total if total > 0 else 0.0)
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
    *,
    min_quality_rows: int | None = None,
    quarantined: bool = False,
) -> QualityReport:
    """生成 / 更新运行的质量报告（幂等：run_id 唯一，重复调用更新同一行）。

    报告字段（SPEC 5.2 quality_reports）：status / 行列数 / reject_rate /
    valid_sink / error_sink / rules（契约规则引用）/ diagnostics（错误码统计等聚合事实）。

    Args:
        contract: QualityContract（阈值 + 规则引用）。
        outcome: 引擎回传的分流聚合事实。
        min_quality_rows: 最低质量行数（缺省取契约值 → 100）。
        quarantined: 是否已被隔离（隔离即 FAILED，见 `classify_quality`）。
    """
    routed = route_outcome(contract, outcome)
    status, reject_rate = classify_quality(
        contract, routed, min_quality_rows=min_quality_rows, quarantined=quarantined
    )
    diagnostics: list[dict[str, Any]] = [
        {"kind": "error_codes", "counts": dict(routed.error_codes)},
    ]
    if quarantined:
        diagnostics.append(
            {"kind": "quarantined", "detail": "运行已被监督隔离，质量判定强制 FAILED（FR-46）"}
        )
    if status == STATUS_INCOMPLETE and routed.total_rows is not None:
        threshold_rows = (
            min_quality_rows
            if min_quality_rows is not None
            else int(contract.get("min_quality_rows") or DEFAULT_MIN_QUALITY_ROWS)
        )
        diagnostics.append(
            {
                "kind": "insufficient_rows",
                "detail": f"总行数 {routed.total_rows} 低于最低质量行数 {threshold_rows}",
            }
        )
    elif status == STATUS_INCOMPLETE:
        diagnostics.append({"kind": "engine_metrics_missing", "detail": "引擎未回传总行数"})
    existing = (
        (await session.execute(select(QualityReport).where(QualityReport.run_id == run_id)))
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
    """契约规则引用（只引名称 / 表达式 / 错误码，不回传业务数据；FR-22 质量契约要素）。

    `code` 参与输出是因为**错误码分布按 `rules[].code` 聚合**（PRD 第 10 章）——
    缺它则报告只有规则名、无法给出错误码分布（T-SHADOW-01 起生成侧已带该标签）。
    """
    rules: list[dict[str, Any]] = []
    for predicate in contract.get("predicates") or []:
        if isinstance(predicate, dict):
            rule = {
                "name": str(predicate.get("name", "")),
                "expression": str(predicate.get("expression", "")),
            }
            if predicate.get("code"):
                rule["code"] = str(predicate["code"])
            rules.append(rule)
    return rules


__all__ = [
    "DEFAULT_MIN_QUALITY_ROWS",
    "STATUS_FAILED",
    "STATUS_INCOMPLETE",
    "STATUS_PASSED",
    "STATUS_WARNING",
    "WARNING_RATIO",
    "QualityOutcome",
    "classify_quality",
    "generate_quality_report",
    "route_outcome",
]
