"""M-N 审计证据 — API 路由（查询证据流 / 事件列表 / 整流验证）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N / 第 5.2 章 harness_evidence_streams /
第 6.4 章错误码 / 第 8 章「安全 · 多租户」
覆盖 FR：FR-36（证据账本查询与整流验证）、FR-66（项目隔离）
对应 AC：AC-09（证据可验证）、AC-12（跨项目 404）、AC-02（响应零明文）

鉴权：全部端点带 `project_id` 路径参数，由 `require_project_member` 校验成员关系
（非成员 / 跨项目统一 404，不泄露资源存在性）。证据账本是**读多写少**的审计面，
本阶段不开放写入端点——事件由域内服务在治理动作发生时追加（`evidence.append`）。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.actor_resolver import ActorContext
from etl_agent.auth.dependencies import require_project_member
from etl_agent.config import Settings
from etl_agent.evidence import stream as stream_service
from etl_agent.evidence.schemas import (
    EvidenceEventListResponse,
    EvidenceEventView,
    EvidenceStreamView,
    VerifyResponse,
)
from etl_agent.evidence.verify import verify_stream
from etl_agent.infrastructure.database import get_session
from etl_agent.infrastructure.models import HarnessEvidenceEvent

router = APIRouter(prefix="/api/v1", tags=["evidence"])


def _event_view(row: HarnessEvidenceEvent) -> EvidenceEventView:
    """事件行 → 视图（逐字段显式列出，确保正文永不外泄，AC-02）。"""
    return EvidenceEventView(
        id=row.id,
        stream_id=row.stream_id,
        sequence=row.sequence,
        payload_sha256=row.payload_sha256,
        previous_hash=row.previous_hash,
        event_hash=row.event_hash,
        signature_algorithm=row.signature_algorithm,
        signature_key_id=row.signature_key_id,
        occurred_at=row.occurred_at,
        actor_id=row.actor_id,
        event_type=row.event_type,
        risk_level=row.risk_level,
        policy_decision_id=row.policy_decision_id,
        trace_id=row.trace_id,
    )


@router.get(
    "/projects/{project_id}/evidence-stream",
    response_model=EvidenceStreamView,
)
async def get_evidence_stream(
    project_id: uuid.UUID,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> EvidenceStreamView:
    """查询项目的证据流（不存在即 404；不自动建流）。"""
    stream = await stream_service.require_stream(session, project_id)
    return EvidenceStreamView(
        id=stream.id,
        project_id=stream.project_id,
        head_hash=stream.head_hash,
        created_at=stream.created_at,
    )


@router.get(
    "/projects/{project_id}/evidence-stream/events",
    response_model=EvidenceEventListResponse,
)
async def list_evidence_events(
    project_id: uuid.UUID,
    after_sequence: int = Query(default=0, ge=0, description="仅返回该序号之后的事件"),
    limit: int = Query(default=100, ge=1, le=1000),
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> EvidenceEventListResponse:
    """按序号增量拉取事件（只追加流的天然游标，避免深分页偏移）。"""
    stream = await stream_service.require_stream(session, project_id)
    result = await session.execute(
        select(HarnessEvidenceEvent)
        .where(
            HarnessEvidenceEvent.stream_id == stream.id,
            HarnessEvidenceEvent.sequence > after_sequence,
        )
        .order_by(HarnessEvidenceEvent.sequence.asc())
        .limit(limit)
    )
    rows = list(result.scalars().all())
    return EvidenceEventListResponse(
        stream_id=stream.id,
        event_count=len(rows),
        events=[_event_view(row) for row in rows],
    )


@router.get(
    "/projects/{project_id}/evidence-stream/verify",
    response_model=VerifyResponse,
)
async def verify_evidence_stream(
    project_id: uuid.UUID,
    request: Request,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> VerifyResponse:
    """整流验证：重算全链并回传报告（含断点定位）。

    验证失败**不回 4xx**：报告本身就是业务结果（`valid=false` + `errors[]`），
    调用方需要拿到完整报告才能定位断链位置（SPEC 3.2 M-N）。
    """
    settings: Settings = request.app.state.settings
    report = await verify_stream(
        session, project_id=project_id, public_keys=settings.harness_public_key_list
    )
    return VerifyResponse(**report.to_dict())


__all__ = ["router"]
