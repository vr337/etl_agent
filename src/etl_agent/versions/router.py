"""M-H 不可变版本与设计审批 — API 路由（API-07 + 派生 / 审批 / Diff）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-07 / 第 6.2 章接口契约 / 第 8 章「安全 · 多租户」
覆盖 FR：FR-26（冻结 / 派生 / 乐观锁）、FR-27（版本 Diff）、FR-28（设计审批）、FR-66（项目隔离）
对应 AC：AC-04（不可变版本摘要）、AC-01（审批职责分离）、AC-17（版本 Diff）、AC-12（跨项目 403）

鉴权：路径中不含 `project_id`，一律由版本 / 审批申请回溯所属项目后做成员校验
（`require_actor_for_project`），对非成员统一 403、对不存在统一 404，不泄露资源存在性。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.dependencies import require_actor_for_project
from etl_agent.infrastructure.database import get_session
from etl_agent.infrastructure.models import PipelineVersion
from etl_agent.versions import approval as approval_service
from etl_agent.versions import artifacts as artifact_service
from etl_agent.versions import diff as diff_service
from etl_agent.versions import service as version_service
from etl_agent.versions.errors import VersionStateConflict
from etl_agent.versions.schemas import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalRequestResponse,
    DeriveResponse,
    FreezeRequest,
    FreezeResponse,
    PipelineVersionResponse,
    VersionArtifact,
    VersionDiffResponse,
)

router = APIRouter(prefix="/api/v1", tags=["versions"])


async def _artifact_rows(session: AsyncSession, version_id: uuid.UUID) -> list[VersionArtifact]:
    """制品摘要行（只有类型 + SHA256）。"""
    rows = await artifact_service.list_artifacts(session, version_id)
    return [VersionArtifact(artifact_type=item.artifact_type, sha256=item.sha256) for item in rows]


def _version_response(
    version: PipelineVersion, artifacts: list[VersionArtifact]
) -> PipelineVersionResponse:
    return PipelineVersionResponse(
        id=version.id,
        pipeline_id=version.pipeline_id,
        version_number=version.version_number,
        status=version.status,
        artifact_sha256=version.artifact_sha256,
        parent_version_id=version.parent_version_id,
        lock_version=version.lock_version,
        artifacts=artifacts,
    )


@router.get("/pipeline-versions/{version_id}", response_model=PipelineVersionResponse)
async def get_version(
    version_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> PipelineVersionResponse:
    """查询版本状态与制品摘要。"""
    version, project_id = await version_service.load_version(session, version_id)
    await require_actor_for_project(request, session, project_id)
    return _version_response(version, await _artifact_rows(session, version.id))


@router.post("/pipeline-versions/{version_id}/freeze", response_model=FreezeResponse)
async def freeze_version(
    version_id: uuid.UUID,
    request: Request,
    payload: FreezeRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> FreezeResponse:
    """冻结不可变版本（API-07）：写制品 SHA256 摘要并转 VALIDATED（FR-26）。

    正文来源二选一：请求体显式给出，或从 `etl_checkpoint` 取回该版本候选正文
    （S5 吸收 2：业务库只存摘要，正文只在 Checkpoint）。
    已冻结且摘要一致 → 幂等返回；摘要不一致 → 409（禁原地覆盖）。
    """
    version, project_id = await version_service.load_version(session, version_id)
    await require_actor_for_project(request, session, project_id)

    body = payload or FreezeRequest()
    plan = body.plan
    hocon = body.hocon
    if plan is None or hocon is None:
        from etl_agent.generation.service import load_candidate_content

        stored = await load_candidate_content(
            session,
            version.id,
            request.app.state.settings,
            getattr(request.app.state, "checkpointer", None),
        )
        if stored:
            plan = plan or stored.get("etl_plan")
            hocon = hocon or stored.get("hocon")
    if not plan or not hocon:
        raise VersionStateConflict("缺少候选正文：请求体未提供且无法从 Checkpoint 取回该版本候选")

    digest = await artifact_service.freeze_version_artifacts(
        session,
        version.id,
        plan,
        hocon,
        body.quality_contract,
        expected_lock_version=body.expected_lock_version,
    )
    await session.commit()
    await session.refresh(version)
    return FreezeResponse(
        id=version.id,
        artifact_sha256=digest,
        status=version.status,
        artifacts=await _artifact_rows(session, version.id),
    )


@router.post(
    "/pipeline-versions/{version_id}/derive", response_model=DeriveResponse, status_code=201
)
async def derive_version(
    version_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> DeriveResponse:
    """派生新版本（FR-26）：任何变更都必须派生，禁止原地覆盖已冻结版本。

    路径无 `project_id`，故先由版本回溯所属项目再做成员校验（与审批 / commit 一致）。
    """
    source, project_id = await version_service.load_version(session, version_id)
    await require_actor_for_project(request, session, project_id)
    derived = await version_service.derive_version(session, source.id, project_id=project_id)
    await session.commit()
    return DeriveResponse(
        id=derived.id,
        pipeline_id=derived.pipeline_id,
        version_number=derived.version_number,
        status=derived.status,
        parent_version_id=derived.parent_version_id,
    )


@router.post(
    "/pipeline-versions/{version_id}/approval-requests",
    response_model=ApprovalRequestResponse,
    status_code=201,
)
async def create_approval_request(
    version_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ApprovalRequestResponse:
    """创建设计审批申请（FR-28）：绑定 `artifact_sha256` 并转 WAITING_APPROVAL。"""
    version, project_id = await version_service.load_version(session, version_id)
    actor = await require_actor_for_project(request, session, project_id)
    approval = await approval_service.create_approval_request(
        session, version_id=version.id, project_id=project_id, requested_by=actor.user_id
    )
    await session.commit()
    return ApprovalRequestResponse(
        id=approval.id,
        pipeline_version_id=approval.pipeline_version_id,
        artifact_sha256=approval.artifact_sha256,
        status=approval.status,
        decision=approval.decision,
        requested_by=approval.requested_by,
    )


@router.post("/approval-requests/{request_id}/decisions", response_model=ApprovalDecisionResponse)
async def decide_approval(
    request_id: uuid.UUID,
    payload: ApprovalDecisionRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ApprovalDecisionResponse:
    """设计审批决策（FR-28）：APPROVE/REJECT + 理由留痕；申请人禁自批（PRD 2.3-1）。"""
    approval, project_id = await approval_service.load_approval(session, request_id)
    actor = await require_actor_for_project(request, session, project_id)
    outcome = await approval_service.decide_approval(
        session,
        request=approval,
        actor=actor,
        decision=payload.decision,
        reason=payload.reason,
    )
    await session.commit()
    return ApprovalDecisionResponse(
        id=outcome.request.id,
        pipeline_version_id=outcome.request.pipeline_version_id,
        status=outcome.request.status,
        decision=outcome.request.decision,
        decided_by=outcome.request.decided_by,
        version_status=outcome.version_status,
    )


@router.get("/pipeline-versions/{version_id}/diff", response_model=VersionDiffResponse)
async def diff_versions(
    version_id: uuid.UUID,
    against_version_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> VersionDiffResponse:
    """版本 Diff（FR-27）：摘要级差异恒可用；正文级差异视 Checkpoint 可得性。"""
    version, project_id = await version_service.load_version(session, version_id)
    await require_actor_for_project(request, session, project_id)
    other, _ = await version_service.load_version(session, against_version_id, project_id)

    digests = diff_service.diff_artifact_digests(
        await artifact_service.artifact_digest_map(session, other.id),
        await artifact_service.artifact_digest_map(session, version.id),
    )

    from etl_agent.generation.service import load_candidate_content

    settings = request.app.state.settings
    checkpointer = getattr(request.app.state, "checkpointer", None)
    before = await load_candidate_content(session, other.id, settings, checkpointer)
    after = await load_candidate_content(session, version.id, settings, checkpointer)
    content_available = bool(before and after)

    schema_diff = quality_diff = hocon_diff = None
    if content_available and before is not None and after is not None:
        plan_diff = diff_service.diff_plans(before.get("etl_plan"), after.get("etl_plan"))
        schema_diff = plan_diff["schema"]
        quality_diff = plan_diff["quality_contract"]
        hocon_diff = diff_service.diff_hocon(before.get("hocon"), after.get("hocon"))

    return VersionDiffResponse(
        version_id=version.id,
        against_version_id=other.id,
        artifact_digests=digests,
        content_available=content_available,
        schema_diff=schema_diff,
        quality_contract_diff=quality_diff,
        hocon_diff=hocon_diff,
    )
