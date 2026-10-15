"""M-L 受管回滚 — API 路由（API-13 申请 / 审批 / 查询 / 到期清理扫描）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-13 / 第 6.2 章接口契约 /
第 6.4 章错误码 / 第 8 章「安全 · 多租户」
覆盖 FR：FR-48（回滚申请 / 审批 / 执行 / 制品清单 / 到期清理）、FR-66（项目隔离）
对应 AC：AC-08（回滚走受管链路）、AC-12（跨项目 404 / 403）、AC-02（响应零明文）

鉴权：申请端点由路径 `run_id` 回溯项目；审批 / 查询端点由申请回溯项目后校验成员。
**执行不由本路由触发**：`POST /rollbacks/{id}/execute` 只创建 Harness 副作用准备单
（PDP 默认 P0 → 双人审批），真正的数据面动作在 Worker 消费令牌后发生（禁止事项 17）。

路由只做编排与鉴权，安全判决（PDP / 审批 / 状态机）全部在服务层完成。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.actor_resolver import ActorContext
from etl_agent.auth.dependencies import require_actor_for_project, require_project_member
from etl_agent.infrastructure.database import get_session
from etl_agent.infrastructure.models import RollbackCleanupRequest, RollbackRequest
from etl_agent.infrastructure.pagination import paginate
from etl_agent.rollback import service as rollback_service
from etl_agent.rollback.cleanup import scan_expired_rollbacks
from etl_agent.rollback.errors import RollbackNotFound
from etl_agent.rollback.schemas import (
    CleanupScanResponse,
    RollbackCleanupRequestInput,
    RollbackCleanupView,
    RollbackCreateRequest,
    RollbackDecisionRequest,
    RollbackListView,
    RollbackResponse,
)

router = APIRouter(prefix="/api/v1", tags=["rollback"])


async def _view(session: AsyncSession, row: RollbackRequest) -> RollbackResponse:
    """回滚申请行 → 响应视图（逐字段显式列出，避免顺手带出未登记列）。

    `session.flush()` 后 `updated_at`（server onupdate）处于过期态，直接读取会触发
    async 惰性加载（MissingGreenlet）。此处先 `refresh` 固化全部列再组装视图，
    使视图始终是「一次可读快照」，不依赖调用方在何处 commit。
    """
    if row in session.dirty or not row.__dict__.get("updated_at"):
        await session.refresh(row)
    return RollbackResponse(
        id=row.id,
        run_id=row.run_id,
        status=row.status,
        reason=row.reason,
        requested_by=row.requested_by,
        decided_by=row.decided_by,
        decision=row.decision,
        retention_days=row.retention_days,
        capacity_warn_threshold=row.capacity_warn_threshold,
        artifact_status=row.artifact_status,
        artifact_manifest=row.artifact_manifest,
        executed_at=row.executed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _cleanup_view(row: RollbackCleanupRequest) -> RollbackCleanupView:
    return RollbackCleanupView(
        id=row.id,
        rollback_request_id=row.rollback_request_id,
        status=row.status,
        reason=row.reason,
        requested_by=row.requested_by,
        executed_at=row.executed_at,
        created_at=row.created_at,
    )


def _list_item_view(row: RollbackRequest) -> RollbackListView:
    """回滚行 → 列表项（前端契约口径；不含业务数据，AC-02）。

    `execution_run_id` 即后端 `run_id`；`risk_level` 后端无对应列
    （PDP 判决在 Harness 副作用准备单），如实回 None。
    """
    return RollbackListView(
        id=row.id,
        execution_run_id=row.run_id,
        status=row.status,
        risk_level=None,
        artifact_manifest=row.artifact_manifest,
        created_at=row.created_at,
    )


@router.get(
    "/projects/{project_id}/executions/{run_id}/rollbacks",
    response_model=list[RollbackListView],
)
async def list_rollbacks(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    response: Response,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> list[RollbackListView]:
    """列出某次执行的受管回滚申请（API-13 列表，联调修复新增）。

    校验：运行必须属于路径项目（跨项目统一 404，AC-12，与 POST 申请入口一致）。
    只含治理事实与制品引用，不含业务数据（AC-02）。
    分页 + `X-Total-Count` 响应头；列表体为裸数组（与前端消费方式一致）。
    """
    owner = await rollback_service.run_project(session, run_id)
    if owner != project_id:
        raise RollbackNotFound("执行运行不存在或不属于当前项目")
    rows, total = await paginate(
        session,
        select(RollbackRequest).where(RollbackRequest.run_id == run_id),
        order_by=(RollbackRequest.created_at.desc(), RollbackRequest.id.desc()),
        limit=limit,
        offset=offset,
    )
    response.headers["X-Total-Count"] = str(total)
    return [_list_item_view(row) for (row,) in rows]


@router.post(
    "/projects/{project_id}/executions/{run_id}/rollbacks",
    response_model=RollbackResponse,
    status_code=201,
)
async def create_rollback(
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    payload: RollbackCreateRequest,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> RollbackResponse:
    """API-13 申请受管回滚（PENDING；不触发数据面动作）。

    校验：运行必须属于路径项目（跨项目统一 404，AC-12）；同一运行不得有进行中的回滚。
    """
    owner = await rollback_service.run_project(session, run_id)
    if owner != project_id:
        raise RollbackNotFound("执行运行不存在或不属于当前项目")
    row = await rollback_service.create_rollback_request(
        session,
        payload=rollback_service.CreateRollbackInput(
            run_id=run_id,
            requested_by=actor.user_id,
            reason=payload.reason,
            target_table=payload.target_table,
            shadow_table=payload.shadow_table,
            error_table=payload.error_table,
            retention_days=payload.retention_days,
            capacity_warn_threshold=payload.capacity_warn_threshold,
        ),
    )
    # 先取视图再提交：commit 会使 ORM 属性过期，提交后访问 updated_at 会触发
    # async 惰性加载（MissingGreenlet）——视图是纯治理事实的快照，提前固化即可。
    view = await _view(session, row)
    await session.commit()
    return view


@router.post("/rollbacks/{rollback_id}/decisions", response_model=RollbackResponse)
async def decide_rollback(
    rollback_id: uuid.UUID,
    payload: RollbackDecisionRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RollbackResponse:
    """回滚申请审批（治理层裁决；四眼审批另由 Harness 副作用准备单承载）。"""
    project_id = await rollback_service.load_rollback_project(session, rollback_id)
    actor = await require_actor_for_project(request, session, project_id)
    row = await rollback_service.decide_rollback(
        session,
        rollback_id=rollback_id,
        decision=payload.decision,
        decided_by=actor.user_id,
        reason=payload.reason,
    )
    view = await _view(session, row)
    await session.commit()
    return view


@router.get("/rollbacks/{rollback_id}", response_model=RollbackResponse)
async def get_rollback(
    rollback_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RollbackResponse:
    """查询回滚申请（跨项目 403 / 不存在 404，AC-12）。"""
    project_id = await rollback_service.load_rollback_project(session, rollback_id)
    await require_actor_for_project(request, session, project_id)
    row = await rollback_service.load_rollback(session, rollback_id)
    return await _view(session, row)


@router.get("/rollbacks/{rollback_id}/cleanups", response_model=list[RollbackCleanupView])
async def list_cleanups(
    rollback_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> list[RollbackCleanupView]:
    """查询回滚制品的清理申请（只含引用，不含数据）。"""
    project_id = await rollback_service.load_rollback_project(session, rollback_id)
    await require_actor_for_project(request, session, project_id)
    rows = (
        (
            await session.execute(
                select(RollbackCleanupRequest)
                .where(RollbackCleanupRequest.rollback_request_id == rollback_id)
                .order_by(RollbackCleanupRequest.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [_cleanup_view(row) for row in rows]


@router.post("/projects/{project_id}/rollbacks/cleanup-scan", response_model=CleanupScanResponse)
async def cleanup_scan(
    project_id: uuid.UUID,
    payload: RollbackCleanupRequestInput,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> CleanupScanResponse:
    """到期清理扫描（生成清理申请；执行仍走 Harness 协议）。

    本端点只建「清理申请」这一治理事实，**不**调数据面（禁止事项 17）。
    扫描限本项目：跨项目的制品不进入本项目运维的清理队列（多租户边界）。
    """
    result = await scan_expired_rollbacks(
        session,
        requested_by=actor.user_id,
        retention_days=payload.retention_days,
        project_id=project_id,
    )
    await session.commit()
    return CleanupScanResponse(
        evaluated=result.evaluated,
        created=result.created,
        skipped=result.skipped,
        retention_summary=result.retention.summary(),
        capacity_warning=result.retention.capacity_warning,
        details=list(result.details),
    )
