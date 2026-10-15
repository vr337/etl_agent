"""M-M 评测与安全进化 — API 路由（API-15 运行 Benchmark / 查询运行列表与详情）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-15 / 第 6.2 章接口契约 /
第 6.4 章错误码 / 第 8 章「安全 · 多租户」
覆盖 FR：FR-50（运行版本化 Benchmark 套件）、FR-66（项目隔离）
对应 AC：AC-09（六项门禁）、AC-12（跨项目 404）、AC-02（响应零明文）

鉴权（SPEC 6.2 API-15）：`AUTH_403_ROLE_REQUIRED` —— **仅 SECURITY_APPROVER** 可触发。
Benchmark 是安全策略的度量尺，让任何人可跑等于让任何人可「刷分」；查询端点放宽为项目成员。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.actor_resolver import ActorContext
from etl_agent.auth.dependencies import require_project_member
from etl_agent.benchmark import service as benchmark_service
from etl_agent.benchmark.schemas import (
    BenchmarkRunDetailView,
    BenchmarkRunListView,
    BenchmarkRunRequest,
    BenchmarkRunResponse,
    GateOutcomeView,
    LayerSummaryView,
)
from etl_agent.harness.pdp.engine import PdpEngine
from etl_agent.infrastructure.database import get_session
from etl_agent.observability.logging import current_trace_id

router = APIRouter(prefix="/api/v1", tags=["benchmark"])

# 仅安全审批人可触发 Benchmark（SPEC 6.2 API-15 错误码 AUTH_403_ROLE_REQUIRED）
REQUIRED_ROLE = "SECURITY_APPROVER"


def _view(result: benchmark_service.BenchmarkResult, trace_id: str | None = None) -> BenchmarkRunResponse:
    """结果 → 响应视图（只给摘要与计数，不回传正文）。"""
    return BenchmarkRunResponse(
        run_id=result.run_id,
        suite_id=result.suite_id,
        manifest_version=result.manifest_version,
        manifest_sha256=result.manifest_sha256,
        status=result.status,
        metrics=result.metrics,
        gates=[
            GateOutcomeView(
                key=item.key,
                actual=item.actual,
                operator=item.operator,
                threshold=item.threshold,
                passed=item.passed,
            )
            for item in result.gate_report.gates
        ],
        failures=list(result.gate_report.failures),
        layers=[
            LayerSummaryView(
                layer=name,
                total=item.total,
                passed=item.passed,
                blocked=item.blocked,
            )
            for name, item in sorted(result.layers.items())
        ],
        report_sha256=result.report_sha256,
        trace_id=trace_id,
    )


@router.post(
    "/projects/{project_id}/benchmark-runs",
    response_model=BenchmarkRunResponse,
    status_code=201,
)
async def run_benchmark(
    project_id: uuid.UUID,
    payload: BenchmarkRunRequest,
    request: Request,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> BenchmarkRunResponse:
    """API-15 运行版本化 Benchmark 套件（仅 SECURITY_APPROVER）。

    门禁未全过**不返回 4xx**：`status=FAIL` 是业务结果，调用方需要完整
    六项实际值才能定位是哪一项失守（同证据整流验证的口径）。
    """
    # 职责分离红线：仅人类安全审批人可度量安全策略（FR-51/52 同源口径）
    if REQUIRED_ROLE not in set(actor.harness_roles):
        from etl_agent.auth.errors import RoleRequired

        raise RoleRequired(REQUIRED_ROLE)

    package = request.app.state.pdp_package
    service = benchmark_service.BenchmarkService(
        pdp_engine=PdpEngine(package),
        project_id=project_id,
    )
    result = await service.run(
        session,
        suite_id=payload.suite_id,
        version=payload.manifest_version,
        layers=tuple(item.upper() for item in payload.layers),
    )
    view = _view(result, current_trace_id())
    await session.commit()
    return view


@router.get(
    "/projects/{project_id}/benchmark-runs",
    response_model=BenchmarkRunListView,
)
async def list_benchmark_runs(
    project_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> BenchmarkRunListView:
    """列出项目的 Benchmark 运行（项目成员可读）。"""
    rows = await benchmark_service.list_runs(session, project_id, limit=limit)
    views: list[BenchmarkRunResponse] = []
    for row in rows:
        views.append(
            BenchmarkRunResponse(
                run_id=row.id,
                suite_id=row.suite_id,
                manifest_version=row.manifest_version,
                manifest_sha256=row.manifest_sha256,
                status=row.status,
                metrics=dict(row.metrics or {}),
                gates=[],
                failures=[],
                layers=[],
                report_sha256=row.report_sha256 or "",
            )
        )
    return BenchmarkRunListView(runs=views, total=len(views))


@router.get(
    "/projects/{project_id}/benchmark-runs/{run_id}",
    response_model=BenchmarkRunDetailView,
)
async def get_benchmark_run(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> BenchmarkRunDetailView:
    """查询单次运行（跨项目统一 404，不泄露存在性，AC-12）。"""
    from etl_agent.benchmark.errors import BenchmarkRunNotFound

    row = await benchmark_service.get_run(session, run_id)
    if row.project_id != project_id:
        raise BenchmarkRunNotFound("Benchmark 运行不存在或不属于当前项目")
    return BenchmarkRunDetailView(
        run_id=row.id,
        suite_id=row.suite_id,
        manifest_version=row.manifest_version,
        manifest_sha256=row.manifest_sha256,
        status=row.status,
        metrics=dict(row.metrics or {}),
        report_sha256=row.report_sha256,
        created_at=row.created_at,
    )


__all__ = ["REQUIRED_ROLE", "router"]
