"""M-E 文件资产 — 存储路径管理（引擎只读挂载路径，不暴露 API）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-E / 第 4.4 章初始化与配置要点
覆盖 FR：FR-13（上传路径不暴露给 API，引擎只读受挂载路径）
对应 AC：AC-02（路径信息不外泄）

**路径不暴露（S4 禁止事项 16）**：`storage_path` 只落 `file_assets` 表，
任何 API 响应体都不含该字段（响应只回 `asset_id`）。引擎侧（S7 起）由部署方
把本根目录以只读方式挂载到容器内 `/data/file-assets`，引擎不接触上传原始文件名。
"""
from __future__ import annotations

import uuid
from pathlib import Path

from etl_agent.config import Settings

# 格式 → 磁盘扩展名（存储文件名用 asset_id，原始文件名不入库也不落盘）
_FORMAT_EXTENSIONS: dict[str, str] = {
    "CSV": ".csv",
    "JSON": ".json",
    "JSONL": ".jsonl",
    "XLSX": ".xlsx",
    "PARQUET": ".parquet",
    "ORC": ".orc",
    "XML": ".xml",
    "MARKDOWN": ".md",
    "PDF": ".pdf",
}


def asset_root(settings: Settings) -> Path:
    """文件资产根目录（FR-13：`ETL_AGENT_FILE_ASSET_ROOT`）。"""
    return Path(settings.file_asset_root)


def project_dir(settings: Settings, project_id: uuid.UUID) -> Path:
    """项目隔离目录（AC-12：按 project_id 分目录，天然隔离）。"""
    return asset_root(settings) / str(project_id)


def extension_for(file_format: str) -> str:
    """取格式对应的磁盘扩展名（未知格式回落为 .bin）。"""
    return _FORMAT_EXTENSIONS.get(file_format, ".bin")


def asset_path(settings: Settings, project_id: uuid.UUID, asset_id: uuid.UUID, file_format: str) -> Path:
    """资产落盘路径：`{root}/{project_id}/{asset_id}.{ext}`。"""
    return project_dir(settings, project_id) / f"{asset_id}{extension_for(file_format)}"


def ensure_project_dir(settings: Settings, project_id: uuid.UUID) -> Path:
    """确保项目目录存在并返回。"""
    directory = project_dir(settings, project_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def remove_quietly(path: Path) -> None:
    """删除文件，失败不抛出（上传失败回滚路径：清理不能掩盖原始错误）。"""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
