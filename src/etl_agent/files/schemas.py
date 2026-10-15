"""M-E 文件资产 — API 请求 / 响应模型。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-E / 第 5.2 章 file_assets
覆盖 FR：FR-13（上传与探查）
对应 AC：AC-02（响应**不含** `storage_path`，样本为脱敏值）

`storage_path` 是有意缺席的字段（S4 禁止事项 16）：上传路径只落库，供引擎侧
只读挂载使用，不进任何对外响应。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class FileAssetResponse(BaseModel):
    """上传受理响应（FR-13）。"""

    id: uuid.UUID
    format: str
    size_bytes: int
    row_count: int
    created_at: datetime


class FileAssetListView(BaseModel):
    """文件资产列表项（联调修复新增；前端 `FileAssetView` 对齐）。

    `storage_path` 绝不外泄（S4 禁止事项 16：引擎只读挂载路径）——列表同样只回
    元数据。两处**如实口径**（后端不持有更细粒度的事实，不伪造）：

    - `filename`：取受挂载路径的 basename。`file_assets` 无「原始文件名」列
      （上传时仅用于嗅探格式，不落库），故展示名是落盘名 `<asset_id>.<ext>`；
    - `upload_status`：上传为同步落盘，**任何未完整写入的资产都不会入列**
      （失败即清理半成品并报错），故凡列出即 `COMPLETED`。
    """

    id: uuid.UUID
    filename: str
    size_bytes: int
    row_count: int
    format: str
    upload_status: str = "COMPLETED"
    created_at: datetime


class FileColumn(BaseModel):
    """文件列规格（FR-13：Schema 推断 + 是否已脱敏）。"""

    name: str
    type: str
    nullable: bool
    masked: bool


class FileProbeResponse(BaseModel):
    """文件探查响应（FR-13/14）：`masked_samples` 已脱敏，绝不含明文（AC-02）。"""

    id: uuid.UUID
    format: str
    columns: list[FileColumn]
    masked_samples: list[dict[str, object]]
