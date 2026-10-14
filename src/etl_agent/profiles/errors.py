"""M-D 元数据探查与数据分级 — 领域错误体系。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 6.1 章 API-04
覆盖 FR：FR-12（表级只读 Profile 探查）、FR-14（脱敏样本采样）
对应 AC：AC-02（错误响应不含敏感明文）

本文件两个错误码为 S4 新增（SPEC 6.4 未覆盖探查域），已生成 AMEND-003 草稿待人类审核。
"""
from __future__ import annotations

from etl_agent.auth.errors import BizError


class ProfileNotFound(BizError):
    """Profile 不存在（跨项目视同不存在，不泄露存在性，SPEC 6.4 BIZ_404 语义）。"""

    code = "BIZ_404_PROFILE_NOT_FOUND"
    http_status = 404


class SampleLimitOutOfRange(BizError):
    """采样上限越界（FR-12：sample_limit 1–100，超出即拒绝，不做静默截断）。"""

    code = "BIZ_422_SAMPLE_LIMIT_OUT_OF_RANGE"
    http_status = 422
