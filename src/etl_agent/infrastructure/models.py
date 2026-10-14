"""业务库（etl_agent）全部 ORM 模型（T-02.2）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 5.1/5.2 节（ER 与关键字段）
覆盖 FR：FR-01/02/03、FR-04~11、FR-12~15、FR-16~28、FR-29~37、FR-38~49、FR-50~52、FR-59/65/66
对应 AC：AC-01/02/12；ADR-03（双库分离）、ADR-07（枚举字符串化）

权威来源：
- etl_agent-SPEC_v1.0.3-Baseline 第 5.1/5.2 节（ER 与关键字段）；
- etl_agent-PRD_v1.1.1-Baseline 第 8 章（ER 初稿）与第 10 章（运行报告字段）。

全局硬约束（SPEC 9.10 / ADR-07）：
1. 枚举一律 String 持久化，禁止 PostgreSQL native enum；
2. 项目级资源带 project_id FK，并建 (project_id, status) 或 (project_id, created_at) 索引；
3. sequence 等大整数用 BigInteger；
4. 时间戳统一 TIMESTAMP WITH TIME ZONE；
5. pipeline_versions.lock_version 乐观锁，默认 0；
6. 禁止业务库存 Prompt / 候选 HOCON 正文 / 凭证明文（FR-06 / FR-67）。

T-02.6（UI 原型对照）本阶段补齐字段：
- data_connections.host / port / database_name（视图 2 登记连接表单，端点参数随连接登记，凭据仍走 Secret 引用）；
- agent_runs.source_profile_ids / sink_profile_ids / file_asset_ids（FR-16「绑定 Profile 集合」，视图 3）。

覆盖 FR：FR-01/02/03（M1）、FR-04~11（M2）、FR-12~13/14/15（M3）、FR-16~28（M4-M5）、
FR-29~37（M6）、FR-38~44（M7）、FR-45~49（M8）、FR-50~52（M9）、FR-59/65/66（M11）
对应 AC：AC-01（四眼双唯一前置）、AC-02（凭据零明文）、AC-12（多租户隔离字段边界）；
ADR-03（双库分离：本文件不含 Checkpoint 层表）、ADR-07（枚举字符串化）
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from etl_agent.infrastructure.base import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

# 枚举字符串化（ADR-07）：全部枚举语义列以 String 持久化，禁止 PG native enum——
# 原生枚举增删值需重写表、Alembic 迁移难回滚；合法性由应用层校验 + tests/infrastructure 断言兜底

# ============================================================
# M1 项目与工作区治理（SPEC 5.2.1，4 张）
# 覆盖 FR-01/02/03；对应 AC-01/AC-12。users.subject 是 FR-58 身份注入的查找键（全局唯一）。
# ============================================================


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """登录主体：subject 全局唯一（PRD 2.1 / FR-58 身份注入）。"""

    __tablename__ = "users"

    subject: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)


class Project(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """项目（多租户隔离边界，FR-66）。"""

    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(255), nullable=False)


class ProjectMembership(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """项目成员关系：角色 ADMIN / DATA_ENGINEER / APPROVER / VIEWER（FR-02）。"""

    __tablename__ = "project_memberships"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_memberships_project_user"),
        Index("ix_project_memberships_user_id", "user_id"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)


class ProjectRoleGrant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Harness 职责显式授予（FR-03）：ADMIN 不隐式升级为安全审批人。"""

    __tablename__ = "project_role_grants"
    __table_args__ = (Index("ix_project_role_grants_project_user", "project_id", "user_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    # DATA_ENGINEER / DATA_APPROVER / SECURITY_APPROVER / OPERATOR / AUDITOR
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


# ============================================================
# M2 数据连接与凭据治理（SPEC 5.2.2，8 张）
# 覆盖 FR-04~11；对应 AC-02。凭据零明文：库内只存密文/定位引用（FR-06）。
# ============================================================


class DataConnection(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """异构数据连接：Secret 引用制，不存凭据明文（FR-04 / FR-06）。"""

    __tablename__ = "data_connections"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_data_connections_project_name"),
        Index("ix_data_connections_project_status", "project_id", "status"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # MYSQL / POSTGRESQL / ORACLE / SQL_SERVER / DORIS / HIVE / HDFS / LOCAL_FILE
    connector_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # UNTESTED / TESTING / READY / FAILED
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # T-02.6 补齐：登记连接表单的非密端点参数（凭据仍经 Secret 引用）
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    database_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    secret_ref: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("encrypted_secrets.id", ondelete="SET NULL"), nullable=True
    )
    credential_max_age_days: Mapped[int] = mapped_column(
        Integer, default=90, server_default="90", nullable=False
    )
    credential_warning_days: Mapped[int] = mapped_column(
        Integer, default=14, server_default="14", nullable=False
    )


class EncryptedSecret(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """受管加密凭据：fernet-db 本地密文 / vault-kv-v2 外部定位引用；销毁留墓碑（FR-06）。"""

    __tablename__ = "encrypted_secrets"

    # fernet-db / vault-kv-v2
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vault_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    vault_mount: Mapped[str | None] = mapped_column(String(255), nullable=True)
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SecretRotationRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """凭据轮换申请（FR-07）：新凭据先暂存并真实连通性验证，激活前继续用旧凭据。"""

    __tablename__ = "secret_rotation_requests"
    __table_args__ = (Index("ix_secret_rotation_requests_connection", "connection_id"),)

    connection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("data_connections.id"), nullable=False
    )
    # VALIDATING / WAITING_APPROVAL / APPROVED / ACTIVATING / SUCCEEDED / FAILED
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    new_secret_ref: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("encrypted_secrets.id", ondelete="SET NULL"), nullable=True
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SecretMigrationRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """凭据 Provider 迁移申请（FR-08）：目标 UUID 申请时冻结，Worker 重试写同一路径。"""

    __tablename__ = "secret_migration_requests"
    __table_args__ = (Index("ix_secret_migration_requests_connection", "connection_id"),)

    connection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("data_connections.id"), nullable=False
    )
    source_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    target_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    target_secret_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SecretRotationRollbackRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """轮换回滚申请（FR-07）：恢复旧凭据原始年龄。"""

    __tablename__ = "secret_rotation_rollback_requests"
    __table_args__ = (
        Index("ix_secret_rotation_rollback_requests_rotation", "rotation_request_id"),
    )

    rotation_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("secret_rotation_requests.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SecretDestructionRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """凭据销毁申请（FR-09）：轮换源 / 回滚暂存两类目标的资格判定，留墓碑。"""

    __tablename__ = "secret_destruction_requests"
    __table_args__ = (Index("ix_secret_destruction_requests_secret", "secret_id"),)

    secret_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("encrypted_secrets.id"), nullable=False
    )
    # ROTATION_SOURCE / ROLLBACK_STAGED
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    eligible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    eligibility_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CredentialExpiryAlert(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """凭据到期预警（FR-10）：WARNING / OVERDUE 两级，去重键唯一，支持确认/静默/解决。"""

    __tablename__ = "credential_expiry_alerts"
    __table_args__ = (Index("ix_credential_expiry_alerts_connection", "connection_id"),)

    connection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("data_connections.id"), nullable=False
    )
    # WARNING / OVERDUE
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    credential_age_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    silenced_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CredentialAlertDelivery(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """凭据告警 Webhook 投递（FR-10）：退避冷却 预警 24h / 逾期 6h。"""

    __tablename__ = "credential_alert_deliveries"
    __table_args__ = (Index("ix_credential_alert_deliveries_alert", "alert_id"),)

    alert_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("credential_expiry_alerts.id"), nullable=False
    )
    webhook_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    # PENDING / SENDING / SENT / FAILED
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ============================================================
# M3 元数据探查与文件资产（SPEC 5.2.3，2 张）
# 覆盖 FR-12/13/14/15；对应 AC-02（脱敏样本无明文）。
# ============================================================


class DataProfile(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """表级只读 Profile（FR-12/14/15）：脱敏样本 + 带 SHA256 的分级报告 + 过期时间。"""

    __tablename__ = "data_profiles"
    __table_args__ = (Index("ix_data_profiles_connection_role", "connection_id", "endpoint_role"),)

    connection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("data_connections.id"), nullable=False
    )
    # SOURCE / SINK
    endpoint_role: Mapped[str] = mapped_column(String(16), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(255), nullable=False)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    columns: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    masked_samples: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    classification_report_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FileAsset(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """受管文件资产（FR-13）：上传路径不暴露给 API，引擎只读受挂载路径。"""

    __tablename__ = "file_assets"
    __table_args__ = (Index("ix_file_assets_project_created", "project_id", "created_at"),)

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id"), nullable=False)
    # CSV / JSON / JSONL / XLSX / PARQUET（扩展：ORC / XML / MARKDOWN / PDF）
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)


# ============================================================
# M4-M5 生成与版本（SPEC 5.2.4，6 张）
# 覆盖 FR-16~28；对应 AC-03/04。agent_runs 禁 Prompt/候选正文（FR-67）。
# ============================================================


class Pipeline(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Pipeline：项目内业务命名。"""

    __tablename__ = "pipelines"
    __table_args__ = (Index("ix_pipelines_project", "project_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)


class PipelineVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """不可变版本（FR-26）：修改必须派生新版本；制品 SHA256 冻结；乐观锁 lock_version。"""

    __tablename__ = "pipeline_versions"
    __table_args__ = (
        UniqueConstraint(
            "pipeline_id", "version_number", name="uq_pipeline_versions_pipeline_number"
        ),
        Index("ix_pipeline_versions_pipeline_status", "pipeline_id", "status"),
    )

    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pipelines.id"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # DRAFT / PROFILING / GENERATED / VALIDATED / WAITING_APPROVAL / APPROVED / REJECTED
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("pipeline_versions.id"), nullable=True
    )
    # 乐观锁（FR-26）：并发修改同一版本时以 lock_version 冲突重试，
    # 配合 parent_version_id 派生链保证「不可原地覆盖已冻结版本」
    lock_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)


class PipelineArtifact(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """不可变制品（FR-26）：plan / hocon / quality_contract 三类，SHA256 冻结。"""

    __tablename__ = "pipeline_artifacts"
    __table_args__ = (Index("ix_pipeline_artifacts_version", "pipeline_version_id"),)

    pipeline_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pipeline_versions.id"), nullable=False
    )
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class AgentRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """生成运行（FR-16/17/67）：仅存计数/耗时/非敏感诊断，禁止 Prompt 与候选正文字段。

    对话历史由 LangGraph Checkpoint 承载（etl_checkpoint 库），业务库仅存 pending_question。
    """

    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_pipeline_version", "pipeline_version_id"),)

    # 无 Prompt / 候选正文字段（FR-67 红线，tests/infrastructure 有专项断言）：
    # 对话历史由 LangGraph Checkpoint 承载于 etl_checkpoint 库（ADR-03 双库分离），
    # 业务库只保留 pending_question 与 generation_* 非敏感遥测
    pipeline_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pipeline_versions.id"), nullable=False
    )
    thread_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # QUEUED / RUNNING / WAITING_INPUT / WAITING_APPROVAL / FAILED
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    pending_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    # T-02.6 补齐：FR-16「绑定 PipelineVersion 与 Profile 集合」
    source_profile_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    sink_profile_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    file_asset_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # 生成遥测（FR-17：只记次数/耗时/非敏感诊断）
    generation_backend: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generation_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    generation_candidate_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_repair_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_diagnosis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class ApprovalRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """设计审批申请（FR-28）：绑定 artifact_sha256，APPROVE/REJECT 决策与理由留痕。"""

    __tablename__ = "approval_requests"
    __table_args__ = (Index("ix_approval_requests_pipeline_version", "pipeline_version_id"),)

    pipeline_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pipeline_versions.id"), nullable=False
    )
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # PENDING / APPROVED / REJECTED
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PipelineSchedule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """调度策略（FR-40/41/23）：cron + 时区；BATCH / WATERMARK_INCREMENTAL。"""

    __tablename__ = "pipeline_schedules"

    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pipelines.id"), unique=True, nullable=False
    )
    cron: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    # BATCH / WATERMARK_INCREMENTAL
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    # SKIP / FIRE_ONCE
    misfire_policy: Mapped[str] = mapped_column(String(16), nullable=False)
    misfire_grace_seconds: Mapped[int] = mapped_column(
        Integer, default=120, server_default="120", nullable=False
    )
    watermark_column: Mapped[str | None] = mapped_column(String(255), nullable=True)
    watermark_initial: Mapped[str | None] = mapped_column(String(255), nullable=True)
    watermark_lag_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ============================================================
# M6 Harness 安全执行内核（SPEC 5.2.5，8 张）
# 覆盖 FR-29~37；对应 AC-05。四眼双唯一（FR-32）是职责分离红线（PRD 2.3）。
# ============================================================


class HarnessPolicyDecision(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """PDP 决策（FR-29）：只存规则证据，不存原始数据与 Prompt。"""

    __tablename__ = "harness_policy_decisions"
    __table_args__ = (Index("ix_harness_policy_decisions_project", "project_id", "created_at"),)

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id"), nullable=False)
    # ALLOW / DENY / REQUIRE_APPROVAL / DEFER
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    # P0 / P1 / P2 / P3
    risk_level: Mapped[str] = mapped_column(String(8), nullable=False)
    rule_evidence: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    policy_package_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    policy_package_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ExecutionPreparation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """执行准备单（FR-31）：冻结指纹/影响报告/监督契约/回滚计划；TTL 默认 24h；幂等键项目内唯一。"""

    __tablename__ = "execution_preparations"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_execution_preparations_idem"),
        Index("ix_execution_preparations_project_status", "project_id", "status"),
        Index("ix_execution_preparations_pipeline_version", "pipeline_version_id"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id"), nullable=False)
    policy_decision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("harness_policy_decisions.id"), unique=True, nullable=False
    )
    pipeline_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pipeline_versions.id"), nullable=False
    )
    # DENIED / WAITING_APPROVAL / READY / REJECTED / EXPIRED / COMMITTED
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(8), nullable=False)
    environment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    impact_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    rollback_plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    supervision_contract: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    required_roles: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    required_approvals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    committed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HarnessApprovalDecision(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """四眼审批决策（FR-32）：(preparation_id, role) 与 (preparation_id, decided_by) 双唯一。"""

    __tablename__ = "harness_approval_decisions"
    # 双唯一（FR-32 / AC-01 / PRD 2.3 职责分离红线）：
    # (preparation_id, role) —— 同一职责槽位只能一人，高风险操作禁止一人占多槽；
    # (preparation_id, decided_by) —— 申请人禁止自批，一人只能占一个审批槽
    __table_args__ = (
        UniqueConstraint("preparation_id", "role", name="uq_harness_approvals_prep_role"),
        UniqueConstraint(
            "preparation_id", "decided_by", name="uq_harness_approvals_prep_decided_by"
        ),
    )

    preparation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("execution_preparations.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    decided_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # APPROVE / REJECT
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class HarnessCapabilityGrant(Base, CreatedAtMixin):
    """单次能力令牌（FR-33）：Ed25519 签名；TTL 默认 300s；全局仅可消费 1 次。"""

    __tablename__ = "harness_capability_grants"

    token_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    preparation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("execution_preparations.id"), unique=True, nullable=False
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("execution_runs.id"), unique=True, nullable=True
    )
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    claims: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HarnessCommandOutbox(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """事务 Outbox（FR-35）：业务事实与 Worker 命令同事务落库；PENDING/SENDING/SENT/FAILED。"""

    __tablename__ = "harness_command_outbox"
    __table_args__ = (
        Index("ix_harness_command_outbox_project_status", "project_id", "status"),
        Index("ix_harness_command_outbox_scan", "status", "locked_until"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id"), nullable=False)
    command_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class HarnessSideEffectPreparation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """通用副作用准备单（FR-37）：与 Pipeline 执行同协议（prepare→approve→commit→outbox→worker）。

    结构对齐 execution_preparations；副作用无 Pipeline 版本绑定，以 side_effect_type + resource_ids 描述目标。
    """

    __tablename__ = "harness_side_effect_preparations"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_side_effect_preparations_idem"),
        Index("ix_side_effect_preparations_project_status", "project_id", "status"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id"), nullable=False)
    policy_decision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("harness_policy_decisions.id"), unique=True, nullable=False
    )
    # CANCEL_EXECUTION / ROLLBACK_DORIS / CLEANUP_DORIS / MIGRATE_SECRET /
    # STAGE_SECRET / VALIDATE_SECRET / ACTIVATE_SECRET / ROLLBACK_SECRET / DESTROY_SECRET
    side_effect_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(8), nullable=False)
    environment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    impact_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    rollback_plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    supervision_contract: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    required_roles: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    required_approvals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    committed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HarnessSideEffectApproval(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """副作用四眼审批（FR-37）：同 harness_approval_decisions 双唯一结构。"""

    __tablename__ = "harness_side_effect_approvals"
    __table_args__ = (
        UniqueConstraint(
            "preparation_id", "role", name="uq_side_effect_approvals_prep_role"
        ),
        UniqueConstraint(
            "preparation_id", "decided_by", name="uq_side_effect_approvals_prep_decided_by"
        ),
    )

    preparation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("harness_side_effect_preparations.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    decided_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class HarnessSideEffectCapabilityGrant(Base, CreatedAtMixin):
    """副作用单次能力令牌（FR-37）：同 harness_capability_grants 结构。"""

    __tablename__ = "harness_side_effect_capability_grants"

    token_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    preparation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("harness_side_effect_preparations.id"), unique=True, nullable=False
    )
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    claims: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ============================================================
# M7 执行调度与数据面（SPEC 5.2.6，2 张）
# 覆盖 FR-38~44；对应 AC-06/07。控制面只存治理事实与聚合指标（ADR-01）。
# ============================================================


class ExecutionRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """执行运行（FR-39/69）：与 SeaTunnel 作业一一对应；控制面只存治理事实与聚合指标。"""

    __tablename__ = "execution_runs"
    __table_args__ = (
        Index("ix_execution_runs_pipeline_version_status", "pipeline_version_id", "status"),
    )

    pipeline_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pipeline_versions.id"), nullable=False
    )
    harness_preparation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("execution_preparations.id"), unique=True, nullable=False
    )
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("pipeline_schedules.id"), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # QUEUED / SUBMITTED / RUNNING / SUCCEEDED / FAILED / CANCELLING / CANCELLED
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    seatunnel_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    engine_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # MANUAL / SCHEDULED
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_rows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sink_rows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sink_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    table_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    retry_of_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("execution_runs.id"), nullable=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    failure_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    diagnosis: Mapped[str | None] = mapped_column(Text, nullable=True)
    supervision_action: Mapped[str] = mapped_column(
        String(32), default="CONTINUE", server_default="CONTINUE", nullable=False
    )
    supervision_contract_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecutionEvent(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """执行事件流（FR-39）：level / stage / event_type。"""

    __tablename__ = "execution_events"
    __table_args__ = (Index("ix_execution_events_run_occurred", "run_id", "occurred_at"),)

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("execution_runs.id"), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ============================================================
# M8 运行时监督与受管回滚（SPEC 5.2.7，4 张）
# 覆盖 FR-45~49；对应 AC-08。observed 仅聚合指标、rule_matches 仅规则证据（PRD 10.5）。
# ============================================================


class RuntimeSupervisionSnapshot(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """监督快照（FR-45）：rule_matches 仅规则证据，observed 仅聚合指标，snapshot_sha256。"""

    __tablename__ = "runtime_supervision_snapshots"

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("execution_runs.id"), unique=True, nullable=False
    )
    # CONTINUE / THROTTLE / PAUSE / CANCEL / QUARANTINE
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_level: Mapped[str | None] = mapped_column(String(8), nullable=True)
    rule_matches: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    observed: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    snapshot_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class QualityReport(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """质量契约与分流报告（FR-44）：错误表例外条款见 PRD 10.5。"""

    __tablename__ = "quality_reports"

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("execution_runs.id"), unique=True, nullable=False
    )
    # PASSED / WARNING / FAILED / INCOMPLETE
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    total_rows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    passed_rows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rejected_rows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reject_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    valid_sink: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_sink: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rules: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    diagnostics: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RollbackRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """受管回滚申请（FR-48）：回滚表保留期默认 7 天、容量预警阈值 20。"""

    __tablename__ = "rollback_requests"
    __table_args__ = (Index("ix_rollback_requests_run", "run_id"),)

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("execution_runs.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    retention_days: Mapped[int] = mapped_column(Integer, default=7, server_default="7", nullable=False)
    capacity_warn_threshold: Mapped[int] = mapped_column(
        Integer, default=20, server_default="20", nullable=False
    )
    artifact_manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # AVAILABLE / ROLLED_BACK / ROLLBACK_PENDING / CLEANUP_PENDING / INVALID_METADATA
    artifact_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RollbackCleanupRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """回滚制品到期清理申请（FR-48）。"""

    __tablename__ = "rollback_cleanup_requests"
    __table_args__ = (Index("ix_rollback_cleanup_requests_rollback", "rollback_request_id"),)

    rollback_request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("rollback_requests.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ============================================================
# M9 审计与评测（SPEC 5.2.8，5 张）
# 覆盖 FR-50~52/36；对应 AC-09。证据账本只追加、哈希链防篡改（FR-36）。
# ============================================================


class HarnessEvidenceStream(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """证据流（FR-36）：head_hash 为链头哈希。"""

    __tablename__ = "harness_evidence_streams"
    __table_args__ = (Index("ix_harness_evidence_streams_project", "project_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id"), nullable=False)
    head_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class HarnessEvidenceEvent(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """证据账本事件（FR-36）：哈希链 + 可选 Ed25519 检查点签名；sequence 流内唯一递增。"""

    __tablename__ = "harness_evidence_events"
    __table_args__ = (
        UniqueConstraint("stream_id", "sequence", name="uq_harness_evidence_events_stream_seq"),
    )

    stream_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("harness_evidence_streams.id"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    signature_algorithm: Mapped[str | None] = mapped_column(String(32), nullable=True)
    signature_key_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    signature_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_level: Mapped[str | None] = mapped_column(String(8), nullable=True)
    policy_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("harness_policy_decisions.id"), nullable=True
    )
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class BenchmarkRun(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """版本化 Benchmark 运行（FR-50）：冻结 manifest + 六项门禁指标。"""

    __tablename__ = "benchmark_runs"
    __table_args__ = (Index("ix_benchmark_runs_project_created", "project_id", "created_at"),)

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id"), nullable=False)
    suite_id: Mapped[str] = mapped_column(String(128), nullable=False)
    manifest_version: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # PASS / FAIL
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    report_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SafetyImprovementProposal(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """安全改进提案（FR-51）：类型限定 POLICY / PROMPT / BENCHMARK；状态机不可跳跃。"""

    __tablename__ = "safety_improvement_proposals"
    __table_args__ = (Index("ix_safety_proposals_project_status", "project_id", "status"),)

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id"), nullable=False)
    benchmark_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("benchmark_runs.id"), nullable=True
    )
    # POLICY / PROMPT / BENCHMARK
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    # DRAFT / IN_REVIEW / APPROVED / REJECTED / RELEASED
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SafetyRelease(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """受控发布（FR-52）：影子验证 → 小流量（1%–10%）→ 晋升授权 / 回滚；清单 Ed25519 签名。"""

    __tablename__ = "safety_releases"

    proposal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("safety_improvement_proposals.id"), unique=True, nullable=False
    )
    # SHADOW_RUNNING / CANARY / PROMOTION_APPROVED / ROLLED_BACK
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    canary_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    signature_key_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


# ============================================================
# M11 平台与可观测性（SPEC 5.2.9，2 张）
# 覆盖 FR-38（心跳）/FR-59/60/65/66；对应 AC-11/12。审计事件全字段留痕（PRD 5 审计行）。
# ============================================================


class WorkerHeartbeat(Base, TimestampMixin):
    """Worker 心跳（FR-38）：默认 10s 上报、35s 判失活；capabilities 为能力声明。"""

    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    capabilities: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class AuditEvent(Base, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """审计事件（FR-65/66）：actor / project / action / resource / before / after / reason / trace_id。"""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_project_created", "project_id", "created_at"),
        Index("ix_audit_events_resource", "resource_type", "resource_id"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id"), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
