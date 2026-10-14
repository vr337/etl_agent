"""M-E 文件资产 — API 路由（上传 / 探查）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-E / 第 5.2 章 file_assets
覆盖 FR：FR-13（文件上传与探查：9 种格式、25 MiB / 100,000 行）
对应 AC：AC-02（响应零明文、不含 storage_path）、AC-12（项目隔离）

中间件分层沿用 S2/S3：权限走 `require_project_member` 依赖，不在中间件查业务表。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.actor_resolver import ActorContext
from etl_agent.auth.dependencies import require_project_member
from etl_agent.files import probe as probe_service
from etl_agent.files import service as file_service
from etl_agent.files.schemas import FileAssetResponse, FileColumn, FileProbeResponse
from etl_agent.infrastructure.database import get_session

router = APIRouter(prefix="/api/v1", tags=["file-assets"])


@router.post(
    "/projects/{project_id}/file-assets",
    status_code=201,
    response_model=FileAssetResponse,
)
async def upload_file_asset(
    project_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(description="待导入的文件资产（9 种格式，≤25 MiB / ≤100,000 行）"),
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> FileAssetResponse:
    """上传文件资产（FR-13）。

    响应只含 `id/format/size_bytes/row_count/created_at` —— `storage_path`
    绝不外泄（S4 禁止事项 16：引擎只读挂载路径）。
    """
    asset = await file_service.upload_file(
        session,
        project_id=project_id,
        upload=file,
        settings=request.app.state.settings,
    )
    return FileAssetResponse(
        id=asset.id,
        format=asset.format,
        size_bytes=asset.size_bytes,
        row_count=asset.row_count,
        created_at=asset.created_at,
    )


@router.get(
    "/projects/{project_id}/file-assets/{asset_id}", response_model=FileProbeResponse
)
async def probe_file_asset(
    project_id: uuid.UUID,
    asset_id: uuid.UUID,
    request: Request,
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> FileProbeResponse:
    """文件探查（FR-13/14）：Schema 推断 + 脱敏样本。"""
    result = await probe_service.probe_asset(
        session,
        project_id=project_id,
        asset_id=asset_id,
        settings=request.app.state.settings,
    )
    return FileProbeResponse(
        id=result.asset.id,
        format=result.asset.format,
        columns=[FileColumn.model_validate(column) for column in result.columns],
        masked_samples=result.masked_samples,
    )
