"""质量分流与 QualityReport 用例（T-13.12 / T-13.10 / FR-44）。

覆盖：有效数据 → 影子表、错误数据 → 错误表（原始值 + 违规原因只在数据面错误表）、
四态判定（PASSED / WARNING / FAILED / INCOMPLETE）、拒绝率与错误码统计。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.execution.metrics import (
    ExecutionMetrics,
    TableMetric,
    apply_metrics,
    metrics_from_engine_payload,
)
from etl_agent.execution.routing import (
    STATUS_FAILED,
    STATUS_INCOMPLETE,
    STATUS_PASSED,
    STATUS_WARNING,
    QualityOutcome,
    classify_quality,
    generate_quality_report,
    route_outcome,
)
from etl_agent.infrastructure.models import QualityReport
from tests.execution.conftest import RunFactory

CONTRACT: dict[str, object] = {
    "valid_sink": "orders_wide_shadow",
    "error_sink": "orders_wide_error",
    "max_reject_rate": 0.1,
    "max_rejected_rows": 100,
    "routing": [{"rule": "not_null_id", "sink": "orders_wide_shadow"}],
    "traceability": {
        "source_row_id_column": "id",
        "error_code_column": "error_code",
        "reason_column": "error_reason",
    },
    "predicates": [{"name": "not_null_id", "expression": "id IS NOT NULL"}],
}


def test_routing_valid_to_shadow_and_invalid_to_error() -> None:
    """有效数据落影子表、错误数据落错误表（去向由契约声明，控制面只登记事实）。"""
    routed = route_outcome(
        CONTRACT,
        QualityOutcome(
            total_rows=100,
            passed_rows=90,
            rejected_rows=10,
            valid_sink="engine-default",
            error_sink="engine-default",
        ),
    )
    assert routed.valid_sink == "orders_wide_shadow"
    assert routed.error_sink == "orders_wide_error"


def test_routing_keeps_engine_value_when_contract_silent() -> None:
    """契约未声明去向时保留引擎回传值（不臆造表名）。"""
    routed = route_outcome({}, QualityOutcome(valid_sink="engine-valid", error_sink="engine-err"))
    assert routed.valid_sink == "engine-valid"
    assert routed.error_sink == "engine-err"


def test_classify_passed_warning_failed_incomplete() -> None:
    """四态判定（FR-44 + FR-45 预警比例 0.8）。"""
    assert classify_quality(CONTRACT, QualityOutcome(total_rows=None))[0] == STATUS_INCOMPLETE
    # 拒绝率 5%：低于阈值 10% 但达到 80% 预警线（8%）→ WARNING
    assert classify_quality(CONTRACT, QualityOutcome(total_rows=100, rejected_rows=9))[0] == (
        STATUS_WARNING
    )
    assert classify_quality(CONTRACT, QualityOutcome(total_rows=100, rejected_rows=1))[0] == (
        STATUS_PASSED
    )
    assert classify_quality(CONTRACT, QualityOutcome(total_rows=100, rejected_rows=50))[0] == (
        STATUS_FAILED
    )
    # 拒绝行数阈值优先：行数超限即 FAILED（即使比例很低）
    assert (
        classify_quality(CONTRACT, QualityOutcome(total_rows=1_000_000, rejected_rows=101))[0]
        == STATUS_FAILED
    )


def test_metrics_from_engine_payload() -> None:
    """引擎载荷解析：聚合指标与表级明细（不含业务数据正文）。"""
    metrics = metrics_from_engine_payload(
        {
            "source_rows": 10,
            "sink_rows": 9,
            "source_bytes": 1024,
            "sink_bytes": 900,
            "tables": [{"table": "orders_wide", "rows": 9, "bytes": 900}],
        }
    )
    assert metrics.source_rows == 10 and metrics.sink_bytes == 900
    assert metrics.tables == [TableMetric(table="orders_wide", rows=9, bytes=900)]


def test_metrics_ignores_unknown_and_wrong_types() -> None:
    """未知字段忽略、类型不符不臆造（缺失维度为 None，而不是 0）。"""
    metrics = metrics_from_engine_payload({"source_rows": "many", "tables": "nope"})
    assert metrics.source_rows is None
    assert metrics.tables == []


async def test_generate_quality_report_is_idempotent(
    db_session: AsyncSession, make_run: RunFactory
) -> None:
    """同一运行重复生成只更新同一行（run_id 唯一，SPEC 5.2）。"""
    run = await make_run()
    first = await generate_quality_report(
        db_session, run.id, CONTRACT, QualityOutcome(total_rows=1000, rejected_rows=1)
    )
    second = await generate_quality_report(
        db_session, run.id, CONTRACT, QualityOutcome(total_rows=1000, rejected_rows=500)
    )
    rows = (
        (await db_session.execute(select(QualityReport).where(QualityReport.run_id == run.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert first.id == second.id
    assert rows[0].rejected_rows == 500
    assert rows[0].status == STATUS_FAILED  # 50% > 10% 阈值


async def test_report_records_aggregates_only(
    db_session: AsyncSession, make_run: RunFactory
) -> None:
    """报告只回聚合：规则引用 + 错误码统计 + 去向（原始值留在数据面错误表，PRD 10.5）。"""
    run = await make_run()
    report = await generate_quality_report(
        db_session,
        run.id,
        CONTRACT,
        QualityOutcome(
            total_rows=100,
            passed_rows=98,
            rejected_rows=2,
            error_codes={"NULL_ID": 2},
        ),
    )
    assert report.reject_rate == 0.02
    assert report.rules == [{"name": "not_null_id", "expression": "id IS NOT NULL"}]
    kinds = {item["kind"] for item in report.diagnostics or []}
    assert "error_codes" in kinds
    counts = next(
        item["counts"] for item in report.diagnostics or [] if item["kind"] == "error_codes"
    )
    assert counts == {"NULL_ID": 2}
    assert report.generated_at is not None


async def test_incomplete_report_when_engine_metrics_missing(
    db_session: AsyncSession, make_run: RunFactory
) -> None:
    """引擎未回传总行数 → INCOMPLETE 并记诊断（不把「不知道」写成 PASSED）。"""
    run = await make_run()
    report = await generate_quality_report(db_session, run.id, CONTRACT, QualityOutcome())
    assert report.status == STATUS_INCOMPLETE
    assert report.reject_rate is None
    kinds = {item["kind"] for item in report.diagnostics or []}
    assert "engine_metrics_missing" in kinds


async def test_apply_metrics_writes_table_metrics(
    db_session: AsyncSession, make_run: RunFactory
) -> None:
    """表级指标写回运行并刷新 last_observed_at（FR-39 周期回传）。"""
    run = await make_run()
    run.last_observed_at = datetime(2020, 1, 1, tzinfo=UTC)
    await apply_metrics(
        db_session,
        run,
        ExecutionMetrics(
            source_rows=100,
            sink_rows=90,
            tables=[TableMetric(table="orders_wide", rows=90, bytes=900)],
        ),
    )
    assert run.table_metrics == {"tables": [{"table": "orders_wide", "rows": 90, "bytes": 900}]}
    assert run.last_observed_at is not None
    assert run.last_observed_at.year > 2020


async def test_report_isolated_per_run(db_session: AsyncSession, make_run: RunFactory) -> None:
    """报告按运行隔离（不同运行各自一行）。"""
    first = await make_run()
    second = await make_run()
    await generate_quality_report(db_session, first.id, CONTRACT, QualityOutcome(total_rows=1))
    await generate_quality_report(db_session, second.id, CONTRACT, QualityOutcome(total_rows=2))
    total = (await db_session.execute(select(QualityReport))).scalars().all()
    assert {item.run_id for item in total} == {first.id, second.id}


def test_report_view_never_includes_plaintext() -> None:
    """质量报告字段集合固定（无自由文本正文位），天然不含业务数据。"""
    from etl_agent.execution.schemas import QualityReportView

    fields = set(QualityReportView.model_fields)
    assert fields == {
        "run_id",
        "status",
        "total_rows",
        "passed_rows",
        "rejected_rows",
        "reject_rate",
        "valid_sink",
        "error_sink",
        "rules",
        "diagnostics",
        "generated_at",
    }


def test_generated_contract_threshold_actually_gates() -> None:
    """T-QUALITY-01 回归：**生成侧契约**必须真能卡住判定侧（防 fail-open 复发）。

    判定侧 `classify_quality` 的语义是「未声明阈值 = 放行」，所以阈值必须由
    生成侧（`llm/mock_provider.compliant_plan`）显式写进 `quality_contract`。
    本用例走真实生成路径取契约，锁两条口径：

    1. 演示 seed 的 3/1003 ≈ 0.30% 仍 **PASSED**（取证数字不变）；
    2. 越过契约阈值 5% 必须 **FAILED**（不再靠「没声明阈值」蒙混过关）。
    """
    from etl_agent.llm.mock_provider import compliant_plan

    columns = [
        {"name": "order_id", "type": "bigint", "nullable": False},
        {"name": "customer_id", "type": "bigint", "nullable": False},
        {"name": "amount", "type": "decimal(18, 2)", "nullable": True},
        {"name": "status", "type": "varchar(16)", "nullable": False},
        {"name": "address", "type": "varchar(255)", "nullable": True},
        {"name": "updated_at", "type": "datetime", "nullable": False},
    ]

    def profile(connector_type: str, table: str) -> dict[str, object]:
        return {
            "connector_type": connector_type,
            "connection_id": "11111111-1111-1111-1111-111111111111",
            "schema_name": "source_db",
            "table_name": table,
            "endpoint_role": "SOURCE",
            "columns": columns,
        }

    contract = compliant_plan("需求", profile("MYSQL", "orders"), profile("DORIS", "order_wide"))[
        "quality_contract"
    ]
    assert classify_quality(contract, QualityOutcome(total_rows=1003, rejected_rows=3))[0] == (
        STATUS_PASSED
    )
    assert classify_quality(contract, QualityOutcome(total_rows=1003, rejected_rows=60))[0] == (
        STATUS_FAILED
    )
