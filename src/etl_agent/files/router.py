"""M-E 文件资产 — API 路由（上传 / 探查）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-E / 第 5.2 章 file_assets
覆盖 FR：FR-13（文件上传与探查：9 种格式、25 MiB / 100,000 行）
对应 AC：AC-02（响应零明文、不含 storage_path）、AC-12（项目隔离）

中间件分层沿用 S2/S3：权限走 `require_project_member` 依赖，不在中间件查业务表。
"""

from __future__ import annotations

import uuid
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.auth.actor_resolver import ActorContext
from etl_agent.auth.dependencies import require_project_member
from etl_agent.files import probe as probe_service
from etl_agent.files import service as file_service
from etl_agent.files.schemas import (
    FileAssetListView,
    FileAssetResponse,
    FileColumn,
    FileProbeResponse,
)
from etl_agent.infrastructure.database import get_session
from etl_agent.infrastructure.models import FileAsset
from etl_agent.infrastructure.pagination import paginate

router = APIRouter(prefix="/api/v1", tags=["file-assets"])


@router.get(
    "/projects/{project_id}/file-assets",
    response_model=list[FileAssetListView],
)
async def list_file_assets(
    project_id: uuid.UUID,
    response: Response,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    actor: ActorContext = Depends(require_project_member),
    session: AsyncSession = Depends(get_session),
) -> list[FileAssetListView]:
    """列出项目文件资产元数据（联调修复新增）。

    `file_assets` 自带 `project_id`，直接隔离（FR-66 / AC-12）。
    `storage_path` 绝不外泄（S4 禁止事项 16）；`filename` 只取其 basename。
    总数经 `X-Total-Count` 响应头回传，列表体保持裸数组。
    """
    base = select(FileAsset).where(FileAsset.project_id == project_id)
    rows, total = await paginate(
        session,
        base,
        order_by=(FileAsset.created_at.desc(), FileAsset.id.desc()),
        limit=limit,
        offset=offset,
    )
    response.headers["X-Total-Count"] = str(total)
    return [_list_view(asset) for (asset,) in rows]


def _list_view(asset: FileAsset) -> FileAssetListView:
    """文件资产行 → 列表项（只回元数据；filename 仅取 basename，不暴露受挂载路径）。"""
    # 只取 basename：storage_path 是引擎只读的受挂载路径，绝不能整条外泄
    filename = PurePosixPath(asset.storage_path.replace("\\", "/")).name
    return FileAssetListView(
        id=asset.id,
        filename=filename,
        size_bytes=asset.size_bytes,
        row_count=asset.row_count,
        format=asset.format,
        created_at=asset.created_at,
    )


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
    # 提交语义（T-COMMIT-01）：file_service.upload_file 只 flush 不 commit，`get_session` 退出即回滚
    # → 「返回 2xx 但数据丢失」。必须在本路由落库（与 register_connection / /test 同范式）。
    await session.commit()
    return FileAssetResponse(
        id=asset.id,
        format=asset.format,
        size_bytes=asset.size_bytes,
        row_count=asset.row_count,
        created_at=asset.created_at,
    )


@router.get("/projects/{project_id}/file-assets/{asset_id}", response_model=FileProbeResponse)
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
