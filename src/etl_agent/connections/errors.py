"""M-C 数据连接 — 连接域错误体系。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 6.1 章 API-02/03
覆盖 FR：FR-04（连接登记）、FR-05（连通性测试）、FR-11（轮换策略）
对应 AC：AC-02（错误响应不含凭证明文）

本文件四个错误码为 S3 新增（SPEC 6.4 未覆盖连接域），已生成 AMEND-002 草稿待人类审核。
"""
from __future__ import annotations

from etl_agent.auth.errors import BizError, ValidationError


class ConnectionNotFound(BizError):
    """连接不存在（跨项目视同不存在，不泄露存在性，SPEC 6.4 BIZ_404 语义）。"""

    code = "BIZ_404_CONNECTION_NOT_FOUND"
    http_status = 404


class ConnectionNameDuplicate(BizError):
    """项目内连接名重复（FR-04：项目内唯一名）。"""

    code = "BIZ_409_CONNECTION_NAME_DUPLICATE"
    http_status = 409


class ConnectorUnsupported(BizError):
    """连接器类型暂不支持探查（S3 禁止事项 15：Oracle/SQLServer/Doris/Hive/HDFS 明确错误）。"""

    code = "BIZ_422_CONNECTOR_UNSUPPORTED"
    http_status = 422


class ProbeFailed(BizError):
    """连通性测试失败（错误摘要必须脱敏：不含密码，AC-02）。"""

    code = "BIZ_422_PROBE_FAILED"
    http_status = 422


class InvalidRotationPolicy(ValidationError):
    """轮换策略越界（FR-11：max_age 1–3650、0 < warning < max_age）。"""

    code = "BIZ_400_VALIDATION"
    http_status = 400
