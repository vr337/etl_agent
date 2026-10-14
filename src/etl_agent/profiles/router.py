"""M-D 元数据探查与数据分级 — API 路由（API-04）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-04 / 第 3.2 章 M-D
覆盖 FR：FR-12（表级只读 Profile 探查）、FR-14（脱敏样本）、FR-15（6 级分级）
对应 AC：AC-02（响应零明文）、AC-12（跨项目视同不存在）

中间件分层沿用 S2/S3：权限走 `require_project_member` 依赖，不在中间件查业务表；
连接归属由 `load_connection` 按 `project_id` 过滤（跨项目返回 404，不泄露存在性）。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.actor_resolver import ActorContext
from etl_agent.auth.dependencies import require_project_member
from etl_agent.infrastructure.database import get_session
from etl_agent.infrastructure.models import DataProfile
from etl_agent.profiles import classification as classification_service
from etl_agent.profiles import service as profile_service
from etl_agent.profiles.schemas import ProfileColumn, ProfileCreateRequest, ProfileResponse

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
