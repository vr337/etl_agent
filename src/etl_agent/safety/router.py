"""M-M 评测与安全进化 — API 路由（提案状态机 / 受控发布）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 5.2 章 safety_* /
第 6.4 章错误码 / 第 8 章「安全 · 多租户」
覆盖 FR：FR-51（提案）、FR-52（受控发布）、FR-66（项目隔离）
对应 AC：AC-01（仅 SECURITY_APPROVER 可裁决）、AC-02（零明文）、AC-12（跨项目 404）

鉴权：创建 / 查询需项目成员；**裁决需 SECURITY_APPROVER**（服务层强制，路由不重复实现）；
发布阶段推进需 SECURITY_APPROVER（发布即扩权到生产，属安全维度动作）。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.actor_resolver import ActorContext
from etl_agent.auth.dependencies import require_project_member
from etl_agent.config import Settings
from etl_agent.infrastructure.database import get_session
from etl_agent.safety import proposals as proposal_service
from etl_agent.safety import release as release_service
from etl_agent.safety.errors import SafetyProposalNotFound
from etl_agent.safety.schemas import (
    CanaryRequest,
    ObservationView,
    PromotionRequest,
    ProposalCreateRequest,
    ProposalDecisionRequest,
    ProposalListView,
    ProposalView,
    ReleaseListResponse,
    ReleaseView,
    RollbackRequest,
    ShadowStartRequest,
)
from etl_agent.safety.signing import ReleaseSigner

router = APIRouter(prefix="/api/v1", tags=["safety"])


def _proposal_view(row: object) -> ProposalView:
    """提案行 → 视图（**不含 payload 正文**）。"""
    from etl_agent.infrastructure.models import SafetyImprovementProposal

    assert isinstance(row, SafetyImprovementProposal)
    return ProposalView(
        id=row.id,
        project_id=row.project_id,
        benchmark_run_id=row.benchmark_run_id,
        type=row.type,
        status=row.status,
        title=row.title,
        summary=row.summary,
        created_by=row.created_by,
        decided_by=row.decided_by,
        decision=row.decision,
        decision_reason=row.decision_reason,
        decided_at=row.decided_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _proposal_view_async(
    session: AsyncSession, row: object
) -> ProposalView:
    """异步提案视图：先固化过期列再组装。

    `updated_at`（server onupdate）在 `flush()` 后处于过期态，直接读取会触发 async
    惰性加载（MissingGreenlet）。`refresh` 把它固化成一次可读快照，视图组装因此
    不依赖调用方在何处 commit（S8 `rollback/router.py` 同口径）。
    """
    from etl_agent.infrastructure.models import SafetyImprovementProposal

    assert isinstance(row, SafetyImprovementProposal)
    if row in session.dirty or not row.__dict__.get("updated_at"):
        await session.refresh(row)
    return _proposal_view(row)


def _release_view(row: object) -> ReleaseView:
    from etl_agent.infrastructure.models import SafetyRelease

    assert isinstance(row, SafetyRelease)
    return ReleaseView(
        id=row.id,
        proposal_id=row.proposal_id,
        stage=row.stage,
        canary_percent=row.canary_percent,
        manifest_sha256=row.manifest_sha256,
        signature_key_id=row.signature_key_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _release_view_async(session: AsyncSession, row: object) -> ReleaseView:
    """异步发布视图：先固化过期列再组装（同 `_proposal_view_async`）。"""
    from etl_agent.infrastructure.models import SafetyRelease

    assert isinstance(row, SafetyRelease)
    if row in session.dirty or not row.__dict__.get("updated_at"):
        await session.refresh(row)
    return _release_view(row)


def _signer(request: Request) -> ReleaseSigner:
    """发布签名器（私钥来自人类配置的 `ETL_AGENT_HARNESS_SIGNING_KEY_HEX`）。"""
    settings: Settings = request.app.state.settings
    return ReleaseSigner(settings.harness_signing_key_hex)


def _observation(view: ObservationView | None) -> release_service.ObservationSample | None:
    if view is None:
        return None
    return release_service.ObservationSample(
        sample_size=view.sample_size, anomaly_count=view.anomaly_count
    )


@router.post(
    "/projects/{project_id}/safety-proposals",
    response_model=ProposalView,
    status_code=201,
)
async def create_safety_proposal(
    project_id: uuid.UUID,
    payload: ProposalCreateRequest,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> ProposalView:
    """创建安全改进提案（DRAFT；类型限定 + 非可执行校验）。"""
    row = await proposal_service.create_proposal(
        session,
        proposal_service.ProposalInput(
            project_id=project_id,
            type=payload.type,
            title=payload.title,
            summary=payload.summary,
            payload=payload.payload,
            benchmark_run_id=payload.benchmark_run_id,
            created_by=actor.user_id,
        ),
    )
    view = await _proposal_view_async(session, row)
    await session.commit()
    return view


@router.get(
    "/projects/{project_id}/safety-proposals",
    response_model=ProposalListView,
)
async def list_safety_proposals(
    project_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> ProposalListView:
    """列出项目的安全提案（成员可读）。"""
    rows = await proposal_service.list_proposals(session, project_id, limit=limit)
    views = [await _proposal_view_async(session, row) for row in rows]
    return ProposalListView(proposals=views, total=len(views))


@router.post(
    "/safety-proposals/{proposal_id}/submit",
    response_model=ProposalView,
)
async def submit_safety_proposal(
    proposal_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ProposalView:
    """提交评审（DRAFT → IN_REVIEW）。"""
    from etl_agent.auth.dependencies import require_actor_for_project

    row = await proposal_service.get_proposal(session, proposal_id)
    await require_actor_for_project(request, session, row.project_id)
    updated = await proposal_service.submit_proposal(session, proposal_id)
    view = await _proposal_view_async(session, updated)
    await session.commit()
    return view


@router.post(
    "/safety-proposals/{proposal_id}/decisions",
    response_model=ProposalView,
)
async def decide_safety_proposal(
    proposal_id: uuid.UUID,
    payload: ProposalDecisionRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ProposalView:
    """裁决提案（IN_REVIEW → APPROVED / REJECTED；**仅 SECURITY_APPROVER**）。

    职责校验在服务层（`proposals.decide_proposal`）：路由不重复实现安全判决，
    避免两处口径分叉（SPEC 3.2 模块边界）。
    """
    from etl_agent.auth.dependencies import require_actor_for_project

    row = await proposal_service.get_proposal(session, proposal_id)
    actor = await require_actor_for_project(request, session, row.project_id)
    updated = await proposal_service.decide_proposal(
        session,
        proposal_id=proposal_id,
        decision=payload.decision,
        decided_by=actor.user_id,
        actor_roles=actor.harness_roles,
        reason=payload.reason,
    )
    view = await _proposal_view_async(session, updated)
    await session.commit()
    return view


@router.get(
    "/safety-proposals/{proposal_id}/releases",
    response_model=ReleaseListResponse,
)
async def list_safety_releases(
    proposal_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ReleaseListResponse:
    """列出提案的发布记录（联调修复新增；受控发布页消费）。

    项目隔离经提案回溯（跨项目 404）。`safety_releases.proposal_id` 有唯一约束，
    故每提案最多一条发布记录——本端点是该事实的**集合视图**，未启动发布时返回空集。

    响应体是 `{releases: [...]}` 信封（与前端 `listReleases` 的既有解包方式一致，
    不同于 execution / harness 列表端点所用的裸数组约定）。
    """
    from etl_agent.auth.dependencies import require_actor_for_project

    proposal = await proposal_service.get_proposal(session, proposal_id)
    await require_actor_for_project(request, session, proposal.project_id)
    release = await release_service.get_release_by_proposal(session, proposal_id)
    views = [] if release is None else [await _release_view_async(session, release)]
    return ReleaseListResponse(releases=views, total=len(views))


@router.post(
    "/safety-proposals/{proposal_id}/releases",
    response_model=ReleaseView,
    status_code=201,
)
async def start_safety_release(
    proposal_id: uuid.UUID,
    payload: ShadowStartRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ReleaseView:
    """启动影子验证（提案须 APPROVED；发布清单 Ed25519 签名）。"""
    from etl_agent.auth.dependencies import require_actor_for_project

    row = await proposal_service.get_proposal(session, proposal_id)
    actor = await require_actor_for_project(request, session, row.project_id)
    _require_security_role(actor)
    release = await release_service.start_shadow(
        session,
        proposal_id=proposal_id,
        manifest_sha256=payload.manifest_sha256,
        signer=_signer(request),
    )
    view = await _release_view_async(session, release)
    await session.commit()
    return view


@router.post(
    "/safety-releases/{release_id}/canary",
    response_model=ReleaseView,
)
async def promote_safety_release_canary(
    release_id: uuid.UUID,
    payload: CanaryRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ReleaseView:
    """影子 → 小流量（百分比 1%–10%，越界 422）。"""
    actor, _project_id = await _actor_for_release(request, session, release_id)
    _require_security_role(actor)
    release = await release_service.promote_to_canary(
        session,
        release_id=release_id,
        canary_percent=payload.canary_percent,
        manifest_sha256=payload.manifest_sha256,
        signer=_signer(request),
        observation=_observation(payload.observation),
    )
    view = await _release_view_async(session, release)
    await session.commit()
    return view


@router.post(
    "/safety-releases/{release_id}/promotion",
    response_model=ReleaseView,
)
async def promote_safety_release_production(
    release_id: uuid.UUID,
    payload: PromotionRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ReleaseView:
    """小流量 → 晋升授权（异常率超阈值拒绝）。"""
    actor, _project_id = await _actor_for_release(request, session, release_id)
    _require_security_role(actor)
    observation = _observation(payload.observation)
    assert observation is not None
    release = await release_service.promote_to_production(
        session,
        release_id=release_id,
        manifest_sha256=payload.manifest_sha256,
        signer=_signer(request),
        observation=observation,
    )
    view = await _release_view_async(session, release)
    await session.commit()
    return view


@router.post(
    "/safety-releases/{release_id}/rollback",
    response_model=ReleaseView,
)
async def rollback_safety_release(
    release_id: uuid.UUID,
    payload: RollbackRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ReleaseView:
    """异常 → 受控回滚（ROLLED_BACK 终态）。"""
    actor, _project_id = await _actor_for_release(request, session, release_id)
    _require_security_role(actor)
    release = await release_service.rollback_release(
        session,
        release_id=release_id,
        manifest_sha256=payload.manifest_sha256,
        signer=_signer(request),
        reason=payload.reason,
    )
    view = await _release_view_async(session, release)
    await session.commit()
    return view


def _require_security_role(actor: ActorContext) -> None:
    """发布阶段推进需 SECURITY_APPROVER（发布即扩权，属安全维度）。"""
    from etl_agent.auth.errors import RoleRequired

    if proposal_service.REQUIRED_ROLE not in set(actor.harness_roles):
        raise RoleRequired(proposal_service.REQUIRED_ROLE)


async def _actor_for_release(
    request: Request, session: AsyncSession, release_id: uuid.UUID
) -> tuple[ActorContext, uuid.UUID]:
    """由发布记录回溯项目并校验成员（跨项目统一 404）。"""
    from etl_agent.auth.dependencies import require_actor_for_project

    release = await release_service.get_release(session, release_id)
    proposal = await proposal_service.get_proposal(session, release.proposal_id)
    actor = await require_actor_for_project(request, session, proposal.project_id)
    return actor, proposal.project_id


@router.get(
    "/safety-proposals/{proposal_id}/release",
    response_model=ReleaseView,
)
async def get_safety_release(
    proposal_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ReleaseView:
    """查询提案的发布记录（跨项目 404）。"""
    from etl_agent.auth.dependencies import require_actor_for_project

    proposal = await proposal_service.get_proposal(session, proposal_id)
    await require_actor_for_project(request, session, proposal.project_id)
    release = await release_service.get_release_by_proposal(session, proposal_id)
    if release is None:
        raise SafetyProposalNotFound("该提案尚无发布记录")
    return await _release_view_async(session, release)


__all__ = ["router"]
