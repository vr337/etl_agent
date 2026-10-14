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
