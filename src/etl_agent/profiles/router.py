"""M-D 元数据探查与数据分级 — API 路由（API-04）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-04 / 第 3.2 章 M-D
覆盖 FR：FR-12（表级只读 Profile 探查）、FR-14（脱敏样本）、FR-15（6 级分级）
对应 AC：AC-02（响应零明文）、AC-12（跨项目视同不存在）

中间件分层沿用 S2/S3：权限走 `require_project_member` 依赖，不在中间件查业务表；
连接归属由 `load_connection` 按 `project_id` 过滤（跨项目返回 404，不泄露存在性）。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.actor_resolver import ActorContext
from etl_agent.auth.dependencies import require_project_member
from etl_agent.infrastructure.database import get_session
from etl_agent.infrastructure.models import DataConnection, DataProfile
from etl_agent.infrastructure.pagination import paginate
from etl_agent.profiles import classification as classification_service
from etl_agent.profiles import service as profile_service
from etl_agent.profiles.schemas import (
    ProfileColumn,
    ProfileCreateRequest,
    ProfileResponse,
    ProfileSummaryView,
)

router = APIRouter(prefix="/api/v1", tags=["profiles"])


def _to_response(profile: DataProfile) -> ProfileResponse:
    """Profile 响应：`masked_samples` 已脱敏，绝不含样本明文（AC-02）。"""
    return ProfileResponse(
        id=profile.id,
        connection_id=profile.connection_id,
        endpoint_role=profile.endpoint_role,
        schema_name=profile.schema_name,
        table_name=profile.table_name,
        columns=[
            ProfileColumn.model_validate(entry)
            for entry in classification_service.iter_column_entries(profile.columns)
        ],
        masked_samples=profile.masked_samples or [],
        approximate_row_count=classification_service.approximate_row_count_of(profile.columns),
        classification_report_sha256=profile.classification_report_sha256 or "",
        # create_profile 必然写入 expires_at；此处仅在数据被外部篡改时才取当前时间兜底
        expires_at=profile.expires_at or profile.created_at,
        created_at=profile.created_at,
    )


@router.get(
    "/projects/{project_id}/profiles",
    response_model=list[ProfileSummaryView],
)
async def list_profiles(
    project_id: uuid.UUID,
    response: Response,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> list[ProfileSummaryView]:
    """列出项目下已完成的表级 Profile（联调修复新增）。

    项目隔离：`data_profiles` 无 `project_id` 列，经
    `connection_id → DataConnection.project_id` 派生（FR-66 / AC-12）。
    排序：`created_at` 倒序 + `id` 兜底保证稳定分页。
    总数经 `X-Total-Count` 响应头回传，列表体保持裸数组（与前端消费方式一致）。
    """
    base = (
        select(DataProfile)
        .join(DataConnection, DataProfile.connection_id == DataConnection.id)
        .where(DataConnection.project_id == project_id)
    )
    rows, total = await paginate(
        session,
        base,
        order_by=(DataProfile.created_at.desc(), DataProfile.id.desc()),
        limit=limit,
        offset=offset,
    )
    response.headers["X-Total-Count"] = str(total)
    return [_summary_view(profile) for (profile,) in rows]


def _summary_view(profile: DataProfile) -> ProfileSummaryView:
    """Profile 行 → 列表项（只回聚合事实，不回列明细与脱敏样本）。"""
    entries = classification_service.iter_column_entries(profile.columns)
    classifications: dict[str, int] = {}
    primary_key: list[str] = []
    for entry in entries:
        level = entry.get("effective_classification") or entry.get("classification")
        if isinstance(level, str):
            classifications[level] = classifications.get(level, 0) + 1
        name = entry.get("name")
        if entry.get("is_primary_key") and isinstance(name, str):
            primary_key.append(name)

    has_samples = bool(profile.masked_samples)
    if not has_samples:
        sample_status = "NONE"
    elif profile.expires_at is not None and profile.expires_at <= datetime.now(UTC):
        sample_status = "EXPIRED"
    else:
        sample_status = "AVAILABLE"

    return ProfileSummaryView(
        id=profile.id,
        connection_id=profile.connection_id,
        schema_name=profile.schema_name,
        table_name=profile.table_name,
        endpoint_role=profile.endpoint_role,
        column_count=len(entries),
        primary_key=primary_key or None,
        report_sha256=profile.classification_report_sha256 or "",
        classifications=classifications,
        sample_status=sample_status,
        created_at=profile.created_at,
    )


@router.post(
    "/projects/{project_id}/profiles", status_code=201, response_model=ProfileResponse
)
async def create_profile(
    project_id: uuid.UUID,
    payload: ProfileCreateRequest,
    request: Request,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> ProfileResponse:
    """执行表级只读 Profile 探查（API-04，FR-12/14/15）。"""
    profile = await profile_service.create_profile(
        session,
        project_id=project_id,
        payload=payload,
        settings=request.app.state.settings,
    )
    return _to_response(profile)
