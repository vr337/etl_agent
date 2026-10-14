"""M-E 文件资产 — 上传与加载服务（25 MiB / 100,000 行上限）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-E / 第 5.2 章 file_assets
覆盖 FR：FR-13（文件上传：9 种格式、25 MiB / 100,000 行、上传路径不暴露 API）
对应 AC：AC-02（响应只回 asset_id，storage_path 不入响应）、AC-12（项目隔离）

**两道上限的时点不同（S4 禁止事项 15）**：
- 大小上限在**写入过程中**按块累加判定，超限立即中断并删除半成品 ——
  不先落满盘再检查，避免磁盘被一份超限文件占满；
- 行数上限在**读取过程中**增量计数，超限立即中断 —— 不先读完再判定，
  否则「100,000 行上限」对超大文件毫无约束力。
"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.config import Settings
from etl_agent.files.errors import (
    FileAssetNotFound,
    FileFormatUnsupported,
    FileTooLarge,
    FileTooManyRows,
)
from etl_agent.files.readers import ReaderError, detect_format, reader_for
from etl_agent.files.storage import asset_path, ensure_project_dir, remove_quietly
from etl_agent.infrastructure.models import FileAsset

# 格式嗅探所需头部字节数（覆盖 %PDF- / PAR1 / ORC / PK\x03\x04 / <?xml）
HEAD_BYTES = 16

# 落盘分块大小：1 MiB，兼顾内存占用与写盘次数
CHUNK_SIZE = 1024 * 1024


async def upload_file(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    upload: UploadFile,
    settings: Settings,
) -> FileAsset:
    """接收上传并落盘（API：POST /projects/{project_id}/file-assets，FR-13）。

    流程：嗅探格式 → 分块落盘（大小超限即中断）→ 增量计数（行数超限即中断）
    → 写 `file_assets`（`storage_path` 只入库，绝不进响应）。

    Args:
        session: 请求级数据库会话。
        project_id: 项目隔离边界（AC-12）。
        upload: multipart 上传的文件对象。
        settings: 应用配置（根目录、大小 / 行数上限）。

    Returns:
        FileAsset: 已落库的资产（响应层只暴露 id/format/size_bytes/row_count/created_at）。

    Raises:
        FileFormatUnsupported: 格式不在 9 种之内，或内容无法按该格式解析（422）。
        FileTooLarge:          超过 `ETL_AGENT_FILE_ASSET_MAX_SIZE_MB`（422）。
        FileTooManyRows:       超过 `ETL_AGENT_FILE_ASSET_MAX_ROWS`（422）。
    """
    head = await upload.read(HEAD_BYTES)
    try:
        file_format = detect_format(upload.filename, head)
    except ReaderError as exc:
        # 适配器层的格式异常统一收敛为对外错误码（BIZ_422_FILE_FORMAT_UNSUPPORTED）
        raise FileFormatUnsupported(str(exc)) from exc

    asset_id = uuid.uuid4()
    ensure_project_dir(settings, project_id)
    target = asset_path(settings, project_id, asset_id, file_format)
    max_bytes = settings.file_asset_max_bytes

    try:
        size_bytes = await _write_stream(upload, head, target, max_bytes, settings)
        row_count = _count_rows(target, file_format, settings.file_asset_max_rows)
    except BaseException:
        # 任一校验失败都不得留下半成品：否则重传会不断堆积无效文件
        remove_quietly(target)
        raise

    asset = FileAsset(
        project_id=project_id,
        format=file_format,
        size_bytes=size_bytes,
        row_count=row_count,
        storage_path=str(target),
    )
    session.add(asset)
    await session.flush()
    return asset


async def load_asset(
    session: AsyncSession, project_id: uuid.UUID, asset_id: uuid.UUID
) -> FileAsset:
    """按项目加载文件资产（跨项目视同不存在：统一 404，AC-12）。

    Raises:
        FileAssetNotFound: 资产不存在或不属于该项目。
    """
    result = await session.execute(
        select(FileAsset).where(FileAsset.id == asset_id, FileAsset.project_id == project_id)
    )
    asset = result.scalar_one_or_none()
    if asset is None:
        raise FileAssetNotFound("文件资产不存在")
    return asset


async def _write_stream(
    upload: UploadFile, head: bytes, target: Path, max_bytes: int, settings: Settings
) -> int:
    """分块落盘并累加大小，超限即中断（返回总字节数）。"""
    size_bytes = 0
    with target.open("wb") as handle:
        if head:
            size_bytes += len(head)
            _assert_within_size(size_bytes, max_bytes, settings)
            handle.write(head)
        while True:
            chunk = await upload.read(CHUNK_SIZE)
            if not chunk:
                break
            size_bytes += len(chunk)
            _assert_within_size(size_bytes, max_bytes, settings)
            handle.write(chunk)
    return size_bytes


def _assert_within_size(size_bytes: int, max_bytes: int, settings: Settings) -> None:
    """大小上限判定（在写入过程中调用，超限立即中断）。

    Raises:
        FileTooLarge: 已写入字节数超过上限（FR-13）。
    """
    if size_bytes > max_bytes:
        raise FileTooLarge(
            f"文件超过大小上限 {settings.file_asset_max_size_mb} MiB（已写入 {size_bytes} 字节）"
        )


def _count_rows(path: Path, file_format: str, max_rows: int) -> int:
    """增量计数（超限即中断，S4 禁止事项 15）。

    Raises:
        FileTooManyRows:      行数超过上限。
        FileFormatUnsupported: 内容无法按该格式解析（并入格式错误码，见 files.errors）。
    """
    reader = reader_for(file_format)
    count = 0
    try:
        for _ in reader.iter_rows(path):
            count += 1
            if count > max_rows:
                raise FileTooManyRows(
                    f"文件行数超过上限 {max_rows}（增量计数在超限处中断）"
                )
    except ReaderError as exc:
        raise FileFormatUnsupported(str(exc)) from exc
    return count
