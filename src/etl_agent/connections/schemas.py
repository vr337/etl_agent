"""M-C 数据连接 — API 请求 / 响应模型。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-02/03 / 第 3.2 章 M-C
覆盖 FR：FR-04（连接登记）、FR-05（连通性测试）、FR-11（轮换策略）
对应 AC：AC-02（响应只含 secret_ref 与定位信息，绝不含 password / ciphertext）
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

# 连接器类型白名单（FR-04 / SPEC 5.2 data_connections.connector_type）。
# 基线 8 类 + S11 T-21 追加 CLICKHOUSE（第 9 类）、S3（对象存储，第 10 类）、
# REST_API（HTTP 数据源，第 11 类）；契约扩展登记见 AMEND-011（Draft）。
# 注意：S3 / REST_API 目前只支持**登记 / 探针 / 只读取数**，尚未接入生成引擎的
# HOCON 渲染，故**不进** GATE-002 的可生成白名单（`gate/rules/supported_connectors.py`）。
CONNECTOR_TYPES = frozenset(
    {
        "MYSQL",
        "POSTGRESQL",
        "ORACLE",
        "SQL_SERVER",
        "DORIS",
        "HIVE",
        "HDFS",
        "LOCAL_FILE",
        "CLICKHOUSE",
        "S3",
        "REST_API",
    }
)

# 连接状态机（FR-05）
CONNECTION_STATUSES = frozenset({"UNTESTED", "TESTING", "READY", "FAILED"})

# 默认轮换策略（FR-10/11）
DEFAULT_MAX_AGE_DAYS = 90
DEFAULT_WARNING_DAYS = 14

# 轮换策略边界（FR-11）
MAX_AGE_MIN_DAYS = 1
MAX_AGE_MAX_DAYS = 3650


class ConnectionRegisterRequest(BaseModel):
    """连接登记请求体（API-02）。

    password 只在请求体内传输，落库前即被 SecretProvider 加密（FR-06）。
    """

    name: str = Field(min_length=1, max_length=255)
    connector_type: str = Field(min_length=1, max_length=32)
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    database_name: str | None = Field(default=None, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)
    extra: dict[str, str] | None = None


class ConnectionResponse(BaseModel):
    """连接响应：含定位信息与 secret_ref，**绝不含 password / ciphertext**（AC-02）。"""

    id: uuid.UUID
    name: str
    connector_type: str
    status: str
    host: str | None
    port: int | None
    database_name: str | None
    username: str
    secret_ref: uuid.UUID | None
    credential_max_age_days: int
    credential_warning_days: int
    created_at: datetime


class ConnectionTestResponse(BaseModel):
    """连通性测试受理响应（API-03：后台执行，立即返回 TESTING）。"""

    status: str
    started_at: datetime


class ConnectionPolicyUpdateRequest(BaseModel):
    """轮换策略更新（FR-11：max_age 1–3650、0 < warning < max_age）。"""

    credential_max_age_days: int = Field(ge=MAX_AGE_MIN_DAYS, le=MAX_AGE_MAX_DAYS)
    credential_warning_days: int = Field(ge=1)


# ============================================================
# 凭据治理（T-06）：轮换 / 迁移 / 销毁（路径挂在 connections 之下）
# ============================================================


class RotationCreateRequest(BaseModel):
    """轮换申请（FR-07）：新凭据明文只在请求体，落库即加密。"""

    new_username: str = Field(min_length=1, max_length=255)
    new_password: str = Field(min_length=1, max_length=1024)
    reason: str | None = Field(default=None, max_length=2000)


class RotationResponse(BaseModel):
    """轮换视图：只含状态与引用，绝不含凭据（AC-02）。"""

    id: uuid.UUID
    connection_id: uuid.UUID
    status: str
    new_secret_ref: uuid.UUID | None
    requested_by: uuid.UUID
    approved_by: uuid.UUID | None
    failure_reason: str | None
    created_at: datetime


class RotationListView(BaseModel):
    """轮换列表视图（联调修复新增；前端契约口径，只含状态与引用，绝不含凭据，AC-02）。

    与 `RotationResponse` 的差异：不回显 requested_by / approved_by / new_secret_ref
    等治理标识，改回前端时间轴口径。后端只记录 `created_at`（申请即暂存）与
    `completed_at`（终态），无逐阶段时间列——`activated_at` 仅在 SUCCEEDED 时由
    completed_at 派生，`validated_at` / `rolled_back_at` 如实回 None（不伪造时间轴）。
    """

    id: uuid.UUID
    connection_id: uuid.UUID
    status: str
    reason: str | None = None
    staged_at: datetime | None = None
    validated_at: datetime | None = None
    activated_at: datetime | None = None
    rolled_back_at: datetime | None = None


class RollbackResponse(BaseModel):
    """轮换回滚视图（FR-07）。"""

    id: uuid.UUID
    rotation_request_id: uuid.UUID
    status: str
    reason: str | None
    created_at: datetime


class MigrationCreateRequest(BaseModel):
    """Provider 迁移申请（FR-08）。"""

    target_provider: str = Field(min_length=1, max_length=32)
    reason: str | None = Field(default=None, max_length=2000)


class MigrationResponse(BaseModel):
    """迁移视图（FR-08）：target_secret_id 为申请时冻结的目标 UUID。"""

    id: uuid.UUID
    connection_id: uuid.UUID
    source_provider: str
    target_provider: str
    target_secret_id: uuid.UUID | None
    status: str
    created_at: datetime


class DestructionCreateRequest(BaseModel):
    """销毁申请（FR-09）。"""

    secret_id: uuid.UUID
    target_type: str = Field(min_length=1, max_length=32)
    reason: str | None = Field(default=None, max_length=2000)


class DestructionResponse(BaseModel):
    """销毁视图（FR-09）：墓碑不含秘密，只含资格判定结论。"""

    id: uuid.UUID
    secret_id: uuid.UUID
    target_type: str
    eligible: bool | None
    eligibility_note: str | None
    status: str
    destroyed_at: datetime | None
    created_at: datetime


class S3ObjectView(BaseModel):
    """对象存储中的单个对象元数据（S11 T-21；**不含对象正文**）。"""

    key: str
    size: int
    etag: str | None = None
    last_modified: str | None = None


class S3ObjectListView(BaseModel):
    """对象前缀列举结果（只读；凭据绝不出现在响应中，AC-02）。"""

    prefix: str
    objects: list[S3ObjectView]
    next_continuation_token: str | None = None
