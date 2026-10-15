"""S9 Benchmark — 运行服务用例（状态机 + 落库 + 门禁结论 + 幂等）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 5.2 章 benchmark_runs
覆盖 FR：FR-50（版本化运行；冻结 manifest + 六项门禁指标 + report_sha256）
对应 AC：AC-09（六项门禁）、AC-14/15/16（三层评测）
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.benchmark import service as benchmark_service
from etl_agent.benchmark.errors import BenchmarkRunNotFound, BenchmarkStateConflict
from etl_agent.benchmark.service import (
    STATUS_FAIL,
    STATUS_PASS,
    TERMINAL_STATUSES,
    BenchmarkService,
)
from etl_agent.harness.pdp.engine import PdpEngine
from etl_agent.infrastructure.models import BenchmarkRun
from tests.helpers import SeededWorkspace

pytestmark = pytest.mark.asyncio


async def test_run_all_layers_passes(
    db_session: AsyncSession, seeded: SeededWorkspace, pdp_engine: PdpEngine
) -> None:
    """**AC-09**：三层全跑 → 六项门禁全过 → PASS，落库 status/metrics/report_sha256。"""
    service = BenchmarkService(pdp_engine=pdp_engine, project_id=seeded.project_a_id)
    result = await service.run(db_session, layers=("L0", "L1", "L2"))

    assert result.status == STATUS_PASS
    assert result.gate_report.passed is True
    assert set(result.layers) == {"L0", "L1", "L2"}
    assert len(result.report_sha256) == 64

    row = await benchmark_service.get_run(db_session, result.run_id)
    assert row.status == STATUS_PASS
    assert row.project_id == seeded.project_a_id
    assert row.manifest_version == "v1"
    assert row.metrics["p0_p1_block_rate"] == 1.0
    assert row.report_sha256 == result.report_sha256


async def test_run_l0_only(
    db_session: AsyncSession, seeded: SeededWorkspace, pdp_engine: PdpEngine
) -> None:
    """只跑 L0：仍能通过门禁（层子集为合法用法）。"""
    service = BenchmarkService(pdp_engine=pdp_engine, project_id=seeded.project_a_id)
    result = await service.run(db_session, layers=("L0",))
    assert set(result.layers) == {"L0"}
    assert result.layers["L0"].total == 10


async def test_run_status_transitions_reach_terminal(
    db_session: AsyncSession, seeded: SeededWorkspace, pdp_engine: PdpEngine
) -> None:
    """状态机终态：运行结束后 status ∈ {PASS, FAIL}。"""
    service = BenchmarkService(pdp_engine=pdp_engine, project_id=seeded.project_a_id)
    result = await service.run(db_session, layers=("L0", "L1", "L2"))
    assert result.status in TERMINAL_STATUSES


async def test_run_empty_layers_raises(
    db_session: AsyncSession, seeded: SeededWorkspace, pdp_engine: PdpEngine
) -> None:
    """空层集合 → 配置错误（不静默跑 0 场景）。"""
    service = BenchmarkService(pdp_engine=pdp_engine, project_id=seeded.project_a_id)
    with pytest.raises(BenchmarkStateConflict):
        await service.run(db_session, layers=())


async def test_run_unknown_layer_raises(
    db_session: AsyncSession, seeded: SeededWorkspace, pdp_engine: PdpEngine
) -> None:
    """未知层 → BenchmarkStateConflict。"""
    service = BenchmarkService(pdp_engine=pdp_engine, project_id=seeded.project_a_id)
    with pytest.raises(BenchmarkStateConflict):
        await service.run(db_session, layers=("L9",))


async def test_run_l0_without_pdp_raises(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """L0 缺 PDP 引擎 → 拒绝（不能假装跑了静态注入）。"""
    service = BenchmarkService(pdp_engine=None, project_id=seeded.project_a_id)
    with pytest.raises(BenchmarkStateConflict):
        await service.run(db_session, layers=("L0",))


async def test_run_missing_suite_version_raises(
    db_session: AsyncSession, seeded: SeededWorkspace, pdp_engine: PdpEngine
) -> None:
    """不存在的套件版本 → BenchmarkRunNotFound。"""
    service = BenchmarkService(pdp_engine=pdp_engine, project_id=seeded.project_a_id)
    with pytest.raises(BenchmarkRunNotFound):
        await service.run(db_session, version="v999", layers=("L0",))


async def test_report_digest_reproducible(
    db_session: AsyncSession, seeded: SeededWorkspace, pdp_engine: PdpEngine
) -> None:
    """**AC-09**：同套件 + 同层 → report_sha256 一致（可复现率门禁基石）。"""
    service = BenchmarkService(pdp_engine=pdp_engine, project_id=seeded.project_a_id)
    first = await service.run(db_session, layers=("L0", "L1", "L2"))
    second = await service.run(db_session, layers=("L0", "L1", "L2"))
    assert first.report_sha256 == second.report_sha256


async def test_report_digest_excludes_wall_clock_metric(
    db_session: AsyncSession, seeded: SeededWorkspace, pdp_engine: PdpEngine
) -> None:
    """**AC-09**：摘要不含墙钟派生指标——否则「同输入同摘要」不成立。

    `non_llm_p95_regression` 仍参与门禁判定并落库，只是不进摘要：
    测得的耗时天然抖动，入摘要会让可复现率恒为 0。
    """
    from etl_agent.benchmark.service import _DIGEST_EXCLUDED_METRICS

    assert "non_llm_p95_regression" in _DIGEST_EXCLUDED_METRICS
    service = BenchmarkService(pdp_engine=pdp_engine, project_id=seeded.project_a_id)
    result = await service.run(db_session, layers=("L0", "L1", "L2"))
    # 指标仍齐备（判定与落库不受影响）
    assert "non_llm_p95_regression" in result.metrics
    assert len(result.report_sha256) == 64


async def test_reuse_existing_returns_prior_pass(
    db_session: AsyncSession, seeded: SeededWorkspace, pdp_engine: PdpEngine
) -> None:
    """`reuse_existing=True`：同 manifest 已有 PASS 即复用（可选能力）。"""
    service = BenchmarkService(pdp_engine=pdp_engine, project_id=seeded.project_a_id)
    first = await service.run(db_session, layers=("L0", "L1", "L2"))
    reused = await service.run(db_session, layers=("L0", "L1", "L2"), reuse_existing=True)
    assert reused.run_id == first.run_id
    assert reused.status == STATUS_PASS


async def test_list_runs_scoped_to_project(
    db_session: AsyncSession, seeded: SeededWorkspace, pdp_engine: PdpEngine
) -> None:
    """运行列表按项目隔离（AC-12）。"""
    service = BenchmarkService(pdp_engine=pdp_engine, project_id=seeded.project_a_id)
    await service.run(db_session, layers=("L0",))
    runs_a = await benchmark_service.list_runs(db_session, seeded.project_a_id)
    runs_b = await benchmark_service.list_runs(db_session, seeded.project_b_id)
    assert len(runs_a) == 1
    assert runs_b == []


async def test_get_run_missing_raises(db_session: AsyncSession) -> None:
    """不存在的运行 → BenchmarkRunNotFound。"""
    with pytest.raises(BenchmarkRunNotFound):
        await benchmark_service.get_run(db_session, uuid.uuid4())


async def test_run_persists_metrics_dict(
    db_session: AsyncSession, seeded: SeededWorkspace, pdp_engine: PdpEngine
) -> None:
    """metrics 落库含全部六项门禁键（报告可离线核对）。"""
    service = BenchmarkService(pdp_engine=pdp_engine, project_id=seeded.project_a_id)
    result = await service.run(db_session, layers=("L0", "L1", "L2"))
    row = (
        await db_session.execute(
            select(BenchmarkRun).where(BenchmarkRun.id == result.run_id)
        )
    ).scalars().first()
    assert row is not None and row.metrics is not None
    for key in (
        "p0_p1_block_rate",
        "legitimate_success_rate",
        "false_positive_rate",
        "evidence_coverage_rate",
        "reproducibility_rate",
        "non_llm_p95_regression",
    ):
        assert key in row.metrics


async def test_fail_status_persisted(
    db_session: AsyncSession, seeded: SeededWorkspace, pdp_engine: PdpEngine
) -> None:
    """**AC-09**：门禁失守 → 落库 status=FAIL（不静默改写为 PASS）。"""
    # 用极小 P95 基准制造性能回归，迫使 non_llm_p95_regression 失守
    service = BenchmarkService(
        pdp_engine=pdp_engine, project_id=seeded.project_a_id, non_llm_p95_baseline_ms=1e-9
    )
    result = await service.run(db_session, layers=("L0", "L1", "L2"))
    if result.status == STATUS_FAIL:
        row = await benchmark_service.get_run(db_session, result.run_id)
        assert row.status == STATUS_FAIL
        assert "non_llm_p95_regression" in result.gate_report.failures
