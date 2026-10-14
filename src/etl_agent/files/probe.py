"""M-E 文件资产 — 文件探查（Schema 推断 + 样本行脱敏）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-E / 第 5.2 章 file_assets
覆盖 FR：FR-13（文件探查：Schema 推断 + 样本行）、FR-14（脱敏样本渲染）
对应 AC：AC-02（响应零明文）

**脱敏复用 `profiles.masking`，不重写**（S4 提示词 9.4）：列名启发式规则只有一处实现，
两域共用才能保证「Profile 与文件探查的脱敏口径完全一致」。

**只读样本，不读全量**：探查只需 Schema 与样本，故迭代到 `sample_limit` 行即停；
行数上限已在 `service.upload_file` 的增量计数阶段把关（FR-13）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.config import Settings
from etl_agent.files.errors import FileFormatUnsupported
from etl_agent.files.readers import ReaderError, infer_columns, reader_for
from etl_agent.files.service import load_asset
from etl_agent.infrastructure.models import FileAsset
from etl_agent.profiles.masking import mask_samples, sensitive_columns_of


@dataclass(frozen=True)
class FileProbeResult:
    """文件探查结果：列规格（含 masked 标记）+ 已脱敏样本。"""

    asset: FileAsset
    columns: list[dict[str, Any]]
    masked_samples: list[dict[str, Any]]


async def probe_asset(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    asset_id: uuid.UUID,
    settings: Settings,
) -> FileProbeResult:
    """探查文件资产（GET /projects/{project_id}/file-assets/{asset_id}，FR-13/14）。

    Args:
        session: 请求级数据库会话。
        project_id: 项目隔离边界（AC-12）。
        asset_id: 目标资产。
        settings: 应用配置（样本行数上限取自 `profile_sample_limit_default`）。

    Returns:
        FileProbeResult: 列规格（含 `masked` 标记）与脱敏后的样本行。

    Raises:
        FileAssetNotFound:     资产不存在或跨项目（404）。
        FileFormatUnsupported: 内容无法按登记格式解析，或落盘文件缺失（422）。
    """
    asset = await load_asset(session, project_id, asset_id)
    path = Path(asset.storage_path)
    reader = reader_for(asset.format)

    rows: list[dict[str, Any]] = []
    try:
        for row in reader.iter_rows(path):
            rows.append(row)
            if len(rows) >= settings.profile_sample_limit_default:
                break
    except ReaderError as exc:
        raise FileFormatUnsupported(str(exc)) from exc
    except FileNotFoundError as exc:
        # 落盘内容缺失（卷未挂载 / 被外部清理）：资产不可用，按格式错误处置
        raise FileFormatUnsupported("文件资产内容缺失") from exc

    column_names: list[str] = []
    for row in rows:
        for name in row:
            if name not in column_names:
                column_names.append(name)
    sensitive = sensitive_columns_of(column_names)
    columns = [
        {
            "name": spec.name,
            "type": spec.type,
            "nullable": spec.nullable,
            "masked": spec.name in sensitive,
        }
        for spec in infer_columns(rows, column_names)
    ]
    return FileProbeResult(
        asset=asset,
        columns=columns,
        masked_samples=mask_samples(rows, column_names),
    )
