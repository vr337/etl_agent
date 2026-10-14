"""M-E 文件资产 — 领域错误体系。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 3.2 章 M-E
覆盖 FR：FR-13（文件上传与探查：格式 / 大小 / 行数上限）
对应 AC：AC-02（错误响应不含样本明文）

本文件四个错误码为 S4 新增（SPEC 6.4 未覆盖文件资产域），已生成 AMEND-003 草稿待人类审核。
"""
from __future__ import annotations

from etl_agent.auth.errors import BizError


class FileAssetNotFound(BizError):
    """文件资产不存在（跨项目视同不存在，不泄露存在性，SPEC 6.4 BIZ_404 语义）。"""

    code = "BIZ_404_FILE_ASSET_NOT_FOUND"
    http_status = 404


class FileTooLarge(BizError):
    """单文件超过大小上限（FR-13：默认 25 MiB）。"""

    code = "BIZ_422_FILE_TOO_LARGE"
    http_status = 422


class FileTooManyRows(BizError):
    """单文件超过行数上限（FR-13：默认 100,000 行；读取时增量计数，超限即中断）。"""

    code = "BIZ_422_FILE_TOO_MANY_ROWS"
    http_status = 422


class FileFormatUnsupported(BizError):
    """文件格式不在 9 种受支持格式之内，或内容无法按该格式解析（FR-13）。

    解析失败并入本码而非另设「损坏文件」码：对调用方而言两者的处置相同
    （换一份合法文件重传），单独设码只会增加契约面。
    """

    code = "BIZ_422_FILE_FORMAT_UNSUPPORTED"
    http_status = 422
