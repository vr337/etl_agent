"""M-A/M-C/M-D/M-E/M-F/M-J/M-K 横切配置 — 应用配置加载、生产启动强制校验、SecretProvider / 文件资产 / LLM Provider / Harness 内核 / 执行调度与数据面配置。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-A/M-C/M-D/M-E/M-F/M-J/M-K / 第 4.2 章环境差异 / 第 4.4 章初始化与配置要点 / 第 8 章「性能 · LLM 生成」/ 第 8 章「安全 · 授权与职责分离」/ 第 8 章「高可用 · Worker 可靠性」/ 第 8 章「可观测」
覆盖 FR：FR-58/65（认证双模式）、FR-01（Bootstrap Token）、FR-06（SecretProvider 双模式）、FR-12（采样上限）、FR-13（文件资产上限与挂载根目录）、FR-18（LLM Provider 选择）、FR-21（候选预算硬上限）、FR-29/30（PDP 与签名策略包）、FR-31/33（准备单与 Capability TTL）、FR-38（Worker 心跳与至少一次投递）、FR-40（调度扫描与 misfire 宽限）、FR-59/60（可观测与 /health）
对应 AC：AC-11（生产缺失配置拒绝启动）、AC-02（凭据零明文：密钥只来自环境变量）、AC-04（有限修复预算 ≤3）、AC-05（单次令牌 TTL ≤900s）、AC-06（Worker 可靠投递与调度间隔）
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from etl_agent.infrastructure.database import settings as infra_settings

AUTH_MODES = frozenset({"dev-header", "trusted-gateway"})
ENVIRONMENTS = frozenset({"dev", "staging", "prod"})
SECRET_PROVIDERS = frozenset({"fernet-db", "vault-kv-v2"})
# OpenAI 兼容多供应商（SPEC 2.2）+ mock（仅测试 / 无 Key 环境，S5 提示词 11.2）
LLM_PROVIDERS = frozenset(
    {"openai", "deepseek", "doubao", "kimi", "zhipu", "custom", "mock"}
)

# 生产强制项（AC-11 / SPEC 4.4-3）：HMAC 密钥与 Bootstrap Token 的最小长度
MIN_SECRET_LENGTH = 32

# 有限修复候选预算硬上限（SPEC FR-21 / S6 禁止事项 15）：含初始候选最多 3 份
LLM_MAX_CANDIDATES_HARD_CAP = 3

# Fernet key 的 base64 解码后固定 32 字节（cryptography.fernet 规范）
FERNET_KEY_BYTES = 32

# Harness TTL 边界（SPEC 6.2）：准备单 300s–7d 默认 24h；Capability 30–900s 默认 300s
PREPARATION_TTL_SECONDS_DEFAULT = 24 * 60 * 60
PREPARATION_TTL_SECONDS_MIN = 300
PREPARATION_TTL_SECONDS_MAX = 7 * 24 * 60 * 60
CAPABILITY_TTL_SECONDS_DEFAULT = 300
CAPABILITY_TTL_SECONDS_MIN = 30
CAPABILITY_TTL_SECONDS_MAX = 900

# Ed25519 密钥 hex 长度：32 字节（私钥种子与公钥原始点均为 32 字节）
HARNESS_KEY_HEX_LENGTH = 64

# ---- S7 执行调度与数据面（SPEC 4.3 / 8「高可用 · Worker 可靠性」）----
# Worker 心跳 10s 上报、35s 判失活（FR-38 / SPEC 8 高可用行）
WORKER_HEARTBEAT_INTERVAL_SECONDS_DEFAULT = 10
WORKER_INACTIVE_SECONDS_DEFAULT = 35
# Outbox 扫描 2s、指数退避重试（FR-35 / ADR-06：至少一次投递）
OUTBOX_SCAN_INTERVAL_SECONDS_DEFAULT = 2
OUTBOX_MAX_RETRIES_DEFAULT = 5
OUTBOX_RETRY_BACKOFF_SECONDS_DEFAULT = 5
OUTBOX_LOCK_SECONDS_DEFAULT = 30
# 调度扫描 30s、凭据告警扫描 3600s（FR-40 / FR-10 / SPEC 4.3）
SCHEDULER_SCAN_INTERVAL_SECONDS_DEFAULT = 30
CREDENTIAL_SCAN_INTERVAL_SECONDS_DEFAULT = 3600
# misfire 默认宽限 120s（FR-40）
SCHEDULE_MISFIRE_GRACE_SECONDS_DEFAULT = 120
SCHEDULE_DEFAULT_TIMEZONE = "Asia/Shanghai"
# RUNNING 执行推进：单遍扫描内轮询引擎的最长等待与轮询间隔
EXECUTION_POLL_INTERVAL_SECONDS_DEFAULT = 2
EXECUTION_POLL_MAX_WAIT_SECONDS_DEFAULT = 300
# SeaTunnel 引擎（SPEC 2.1：REST :8080 + CLI --check）
SEATUNNEL_SUBMIT_TIMEOUT_SECONDS_DEFAULT = 30
SEATUNNEL_CHECK_TIMEOUT_SECONDS_DEFAULT = 120
# Capability 令牌交接（Worker 通道）：Redis 键前缀与 TTL（缺省随 Capability TTL）
CAPABILITY_HANDOFF_KEY_PREFIX = "harness:capability"
# OTel 导出超时 10s（SPEC 8「可观测」）
OTEL_EXPORT_TIMEOUT_SECONDS_DEFAULT = 10


class ProductionConfigError(RuntimeError):
    """生产配置缺失或不合法：启动即拒绝（AC-11）。

    失败方向选择「拒绝启动」而非「降级放行」——降级会让生产在没有验签的情况下
    接收身份头，等价于关闭认证（SPEC 8「安全 · 认证」行）。
    """


class Settings(BaseSettings):
    """应用配置：统一 ETL_AGENT_ 前缀（SPEC 4.4-3）。

    数据库 / Redis 连接件与 infrastructure.database.Settings 同源（同一 infra/.env），
    此处经属性复用而非重复定义，避免双份真相。
    """

    model_config = SettingsConfigDict(
        env_prefix="ETL_AGENT_",
        env_file=("infra/.env", ".env"),
        extra="ignore",
    )

    # ---- 环境与认证（SPEC 4.2）----
    env: str = "dev"
    auth_mode: str = "dev-header"
    gateway_hmac_secret: str = ""
    harness_public_keys: str = ""

    # ---- 工作区初始化（FR-01）----
    bootstrap_enabled: bool = False
    bootstrap_token: str = ""

    # ---- SecretProvider（FR-06 / ADR-05）----
    secret_provider: str = "fernet-db"
    fernet_key: str = ""
    fernet_key_version: int = 1
    # 历史主密钥（逗号分隔）：密钥轮换后旧密文仍可读（MultiFernet 解密集合）
    fernet_previous_keys: str = ""
    vault_addr: str = ""
    vault_token: str = ""
    vault_mount: str = "secret"
    # 凭据告警 Webhook（FR-10；未配置则扫描只生成预警记录、不投递）
    alert_webhook_url: str = ""

    # ---- M-D 元数据探查（FR-12）----
    # 采样上限默认值：单次 Profile 最多采样的行数（1–100），越界由 profiles 域拒绝
    profile_sample_limit_default: int = 100

    # ---- M-E 文件资产（FR-13）----
    # 文件资产根目录：S7 起 SeaTunnel 容器以只读方式挂载到 /data/file-assets（FR-13）
    file_asset_root: str = "D:/Documents/Docker/volume/app/etl_agent/file_assets"
    # 单文件大小上限（MiB）与行数上限（FR-13：25 MiB / 100,000 行）
    file_asset_max_size_mb: int = 25
    file_asset_max_rows: int = 100000

    # ---- M-F 生成引擎 · LLM Provider（FR-18/24）----
    # 默认 mock：无 Key 环境与测试可直接跑通状态机；生产禁止 mock（validate_llm 拒绝启动）。
    # 人类在 infra/.env 显式设置真实 provider 后即覆盖此默认值。
    llm_provider: str = "mock"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    # 单次生成请求超时（SPEC 8「性能 · LLM 生成」：≤180s 可配置）
    llm_timeout_seconds: int = 180
    # 有限修复预算（FR-21）：含初始候选，硬上限 3（validate_llm 越界即拒绝启动）
    llm_max_candidates: int = LLM_MAX_CANDIDATES_HARD_CAP

    # ---- 直连覆盖（.env.example 形态，优先级高于连接件拼装）----
    database_url: str | None = None
    checkpoint_database_url: str | None = None
    redis_url: str | None = None

    # ---- M-J Harness 安全执行内核（S6：FR-29/30/31/33）----
    # 策略包（清单 + 分离签名）与验签公钥：缺省路径对应 configs/policy/harness-v2-20260808.*
    policy_package_path: str = "configs/policy/harness-v2-20260808.json"
    policy_package_signature_path: str = "configs/policy/harness-v2-20260808.sig"
    # Ed25519 私钥（仅签名脚本使用，控制面不持有）；逗号分隔的公钥用于验签
    harness_signing_key_hex: str = ""
    # Capability TTL 秒（FR-33：30–900，默认 300；提供方越界即拒绝启动）
    harness_capability_ttl_seconds: int = CAPABILITY_TTL_SECONDS_DEFAULT
    # 准备单 TTL 秒（FR-31：300–7 天，默认 24h；越界即拒绝启动）
    harness_preparation_ttl_seconds: int = PREPARATION_TTL_SECONDS_DEFAULT

    # ---- 数据面引擎 SeaTunnel（S7：FR-20/38）----
    seatunnel_url: str = "http://localhost:8080"
    seatunnel_submit_timeout_seconds: int = SEATUNNEL_SUBMIT_TIMEOUT_SECONDS_DEFAULT
    seatunnel_check_timeout_seconds: int = SEATUNNEL_CHECK_TIMEOUT_SECONDS_DEFAULT

    # ---- Celery Worker / Beat（S7：FR-38/40 / ADR-06）----
    worker_heartbeat_interval_seconds: int = WORKER_HEARTBEAT_INTERVAL_SECONDS_DEFAULT
    worker_inactive_seconds: int = WORKER_INACTIVE_SECONDS_DEFAULT
    outbox_scan_interval_seconds: int = OUTBOX_SCAN_INTERVAL_SECONDS_DEFAULT
    outbox_max_retries: int = OUTBOX_MAX_RETRIES_DEFAULT
    outbox_retry_backoff_seconds: int = OUTBOX_RETRY_BACKOFF_SECONDS_DEFAULT
    outbox_lock_seconds: int = OUTBOX_LOCK_SECONDS_DEFAULT
    scheduler_scan_interval_seconds: int = SCHEDULER_SCAN_INTERVAL_SECONDS_DEFAULT
    credential_scan_interval_seconds: int = CREDENTIAL_SCAN_INTERVAL_SECONDS_DEFAULT
    schedule_misfire_grace_seconds: int = SCHEDULE_MISFIRE_GRACE_SECONDS_DEFAULT
    execution_poll_interval_seconds: int = EXECUTION_POLL_INTERVAL_SECONDS_DEFAULT
    execution_poll_max_wait_seconds: int = EXECUTION_POLL_MAX_WAIT_SECONDS_DEFAULT
    # Capability 令牌交接（Worker 通道）：令牌本体只经 Redis 传递，不落业务库（AC-02）
    capability_handoff_key_prefix: str = CAPABILITY_HANDOFF_KEY_PREFIX
    capability_handoff_ttl_seconds: int = CAPABILITY_TTL_SECONDS_DEFAULT

    # ---- M-O 可观测（S7：FR-59/60）----
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = ""
    otel_export_timeout_seconds: int = OTEL_EXPORT_TIMEOUT_SECONDS_DEFAULT

    @property
    def resolved_database_url(self) -> str:
        """业务库异步连接串（复用 infrastructure.database，S1 已落地）。"""
        return self.database_url or infra_settings.resolved_database_url

    @property
    def resolved_checkpoint_database_url(self) -> str:
        """检查点库异步连接串（复用 infrastructure.database，S1 已落地）。"""
        return self.checkpoint_database_url or infra_settings.resolved_checkpoint_database_url

    @property
    def resolved_redis_url(self) -> str:
        """Redis 连接串：优先 ETL_AGENT_REDIS_URL，否则由连接件拼装。"""
        if self.redis_url:
            return self.redis_url
        password_part = f":{infra_settings.redis_password}@" if infra_settings.redis_password else ""
        return (
            f"redis://{password_part}{infra_settings.redis_host}:"
            f"{infra_settings.redis_port}/{infra_settings.redis_db}"
        )

    @property
    def is_production(self) -> bool:
        return self.env == "prod"

    @property
    def is_mock_llm(self) -> bool:
        """是否使用 mock provider（测试 / 无 Key 环境；生产被 validate_llm 拒绝）。"""
        return self.llm_provider == "mock"

    @property
    def file_asset_max_bytes(self) -> int:
        """单文件字节上限（MiB → 字节，FR-13：默认 25 MiB）。"""
        return self.file_asset_max_size_mb * 1024 * 1024

    def validate_production(self) -> None:
        """生产启动强制校验（AC-11 / SPEC 4.2 prod 行 / 4.4-3）。

        ENV=prod 时三项强制，任一缺失即抛异常拒绝启动：
        1. AUTH_MODE 必须 = trusted-gateway（dev-header 仅限非生产，FR-58/65）；
        2. GATEWAY_HMAC_SECRET 已配置且 ≥32 字节；
        3. HARNESS_PUBLIC_KEYS 已配置（S6 Capability 验签前置，本期占位校验存在性）。
        """
        if self.auth_mode not in AUTH_MODES:
            raise ProductionConfigError(
                f"ETL_AGENT_AUTH_MODE 非法: {self.auth_mode!r}，可选 {sorted(AUTH_MODES)}"
            )
        if not self.is_production:
            return
        problems: list[str] = []
        if self.auth_mode != "trusted-gateway":
            problems.append(
                f"ENV=prod 要求 AUTH_MODE=trusted-gateway，当前为 {self.auth_mode!r}（FR-58：dev-header 仅限非生产）"
            )
        if len(self.gateway_hmac_secret) < MIN_SECRET_LENGTH:
            problems.append(
                f"ETL_AGENT_GATEWAY_HMAC_SECRET 未配置或不足 {MIN_SECRET_LENGTH} 字节（FR-65）"
            )
        if not self.harness_public_keys.strip():
            problems.append("ETL_AGENT_HARNESS_PUBLIC_KEYS 未配置（FR-65 / SPEC 4.4-3）")
        if problems:
            raise ProductionConfigError("生产配置校验失败:\n" + "\n".join(f"  - {p}" for p in problems))

    @property
    def harness_public_key_list(self) -> list[str]:
        """验签公钥列表（逗号分隔，空项过滤；长度非法项一并剔除——宁可拒签也不静默通过）。"""
        keys: list[str] = []
        for item in self.harness_public_keys.split(","):
            key = item.strip()
            if len(key) == HARNESS_KEY_HEX_LENGTH:
                keys.append(key)
        return keys

    def validate_harness(self) -> None:
        """Harness 内核配置校验（S6：AC-11 / AC-05 / FR-29/30/31/33）。

        启动期 fail-fast 的三条红线：
        1. 策略包路径与签名路径**必须显式配置**（不做默认兜底）；
        2. Capability TTL 必须落在 30–900s、准备单 TTL 必须落在 300s–7d——
           越界直接拒绝启动，防止运行期突破 SPEC 6.2 上限；
        3. 生产环境必须配置 Ed25519 私钥（策略包签名）与公钥（验签），
           否则 PDP 没有可信策略可用（FR-30：启动与热切换前强制验签）。
        """
        if not self.policy_package_path.strip():
            raise ProductionConfigError("ETL_AGENT_POLICY_PACKAGE_PATH 未配置（FR-30）")
        if not self.policy_package_signature_path.strip():
            raise ProductionConfigError("ETL_AGENT_POLICY_PACKAGE_SIGNATURE_PATH 未配置（FR-30）")
        if not CAPABILITY_TTL_SECONDS_MIN <= self.harness_capability_ttl_seconds <= CAPABILITY_TTL_SECONDS_MAX:
            raise ProductionConfigError(
                f"ETL_AGENT_HARNESS_CAPABILITY_TTL_SECONDS 必须为 "
                f"{CAPABILITY_TTL_SECONDS_MIN}–{CAPABILITY_TTL_SECONDS_MAX} 秒（FR-33），"
                f"当前为 {self.harness_capability_ttl_seconds}"
            )
        if not (
            PREPARATION_TTL_SECONDS_MIN
            <= self.harness_preparation_ttl_seconds
            <= PREPARATION_TTL_SECONDS_MAX
        ):
            raise ProductionConfigError(
                f"ETL_AGENT_HARNESS_PREPARATION_TTL_SECONDS 必须为 "
                f"{PREPARATION_TTL_SECONDS_MIN}–{PREPARATION_TTL_SECONDS_MAX} 秒（FR-31），"
                f"当前为 {self.harness_preparation_ttl_seconds}"
            )
        if self.is_production:
            if len(self.harness_signing_key_hex.strip()) != HARNESS_KEY_HEX_LENGTH:
                raise ProductionConfigError(
                    "ENV=prod 必须配置 ETL_AGENT_HARNESS_SIGNING_KEY_HEX（Ed25519 32 字节 hex，FR-30）"
                )
            if not self.harness_public_key_list:
                raise ProductionConfigError(
                    "ENV=prod 必须配置 ETL_AGENT_HARNESS_PUBLIC_KEYS（Ed25519 32 字节 hex，FR-30）"
                )

    def validate_worker(self) -> None:
        """执行调度配置校验（S7：FR-38/40 / SPEC 8「高可用 · Worker 可靠性」）。

        启动期 fail-fast 的三条红线：
        1. 四类周期任务间隔必须为正数（Beat 装配前置，零/负间隔会让任务永不触发或忙轮询）；
        2. misfire 宽限必须为正（FR-40：默认 120s，非正宽限使「错过」判定永假）；
        3. SeaTunnel 引擎地址必填（执行面唯一出口，ADR-01：Worker 只认受管指令打到的引擎）；
        4. OTel 启用时端点必填（FR-60：开关开而不配置端点等于静默丢弃遥测）。
        """
        positive: list[tuple[str, int]] = [
            ("ETL_AGENT_WORKER_HEARTBEAT_INTERVAL_SECONDS", self.worker_heartbeat_interval_seconds),
            ("ETL_AGENT_WORKER_INACTIVE_SECONDS", self.worker_inactive_seconds),
            ("ETL_AGENT_OUTBOX_SCAN_INTERVAL_SECONDS", self.outbox_scan_interval_seconds),
            ("ETL_AGENT_OUTBOX_MAX_RETRIES", self.outbox_max_retries),
            ("ETL_AGENT_OUTBOX_RETRY_BACKOFF_SECONDS", self.outbox_retry_backoff_seconds),
            ("ETL_AGENT_OUTBOX_LOCK_SECONDS", self.outbox_lock_seconds),
            ("ETL_AGENT_SCHEDULER_SCAN_INTERVAL_SECONDS", self.scheduler_scan_interval_seconds),
            ("ETL_AGENT_CREDENTIAL_SCAN_INTERVAL_SECONDS", self.credential_scan_interval_seconds),
            ("ETL_AGENT_SCHEDULE_MISFIRE_GRACE_SECONDS", self.schedule_misfire_grace_seconds),
            ("ETL_AGENT_EXECUTION_POLL_INTERVAL_SECONDS", self.execution_poll_interval_seconds),
            ("ETL_AGENT_EXECUTION_POLL_MAX_WAIT_SECONDS", self.execution_poll_max_wait_seconds),
            ("ETL_AGENT_SEATUNNEL_SUBMIT_TIMEOUT_SECONDS", self.seatunnel_submit_timeout_seconds),
            ("ETL_AGENT_SEATUNNEL_CHECK_TIMEOUT_SECONDS", self.seatunnel_check_timeout_seconds),
            ("ETL_AGENT_CAPABILITY_HANDOFF_TTL_SECONDS", self.capability_handoff_ttl_seconds),
            ("ETL_AGENT_OTEL_EXPORT_TIMEOUT_SECONDS", self.otel_export_timeout_seconds),
        ]
        invalid = [f"{name}={value}" for name, value in positive if value <= 0]
        if invalid:
            raise ProductionConfigError(
                "执行调度间隔/超时必须为正数: " + ", ".join(invalid)
            )
        if not self.seatunnel_url.strip():
            raise ProductionConfigError("ETL_AGENT_SEATUNNEL_URL 未配置（FR-38：执行面唯一引擎出口）")
        if self.otel_enabled and not self.otel_exporter_otlp_endpoint.strip():
            raise ProductionConfigError(
                "ETL_AGENT_OTEL_ENABLED=true 时 ETL_AGENT_OTEL_EXPORTER_OTLP_ENDPOINT 必填（FR-60）"
            )

    def validate_bootstrap(self) -> None:
        """Bootstrap 配置校验（FR-01）：启用时 Token 必须 ≥32 字节。

        启动期 fail-fast，避免运行到首次初始化才发现 Token 不合规。
        """
        if self.bootstrap_enabled and len(self.bootstrap_token) < MIN_SECRET_LENGTH:
            raise ProductionConfigError(
                f"ETL_AGENT_BOOTSTRAP_ENABLED=true 时 ETL_AGENT_BOOTSTRAP_TOKEN 必须 ≥{MIN_SECRET_LENGTH} 字节（FR-01）"
            )

    def validate_secrets(self) -> None:
        """SecretProvider 配置校验（FR-06 / ADR-05）。

        - provider 取值限定 fernet-db / vault-kv-v2；
        - fernet-db：FERNET_KEY 必须存在且为合法 Fernet key（base64 32 字节）——
          禁止硬编码密钥（S3 禁止事项 18），启动期缺失即拒绝；
        - vault-kv-v2：VAULT_ADDR 必填（Q5/S-Q7 待定，未部署时不要选该模式）。
        """
        if self.secret_provider not in SECRET_PROVIDERS:
            raise ProductionConfigError(
                f"ETL_AGENT_SECRET_PROVIDER 非法: {self.secret_provider!r}，可选 {sorted(SECRET_PROVIDERS)}"
            )
        if self.secret_provider == "fernet-db":
            self._validate_fernet_key()
        if self.secret_provider == "vault-kv-v2" and not self.vault_addr.strip():
            raise ProductionConfigError(
                "ETL_AGENT_SECRET_PROVIDER=vault-kv-v2 时 ETL_AGENT_VAULT_ADDR 必填（FR-06 / Q5 待定）"
            )

    def validate_llm(self) -> None:
        """LLM Provider 配置校验（FR-18 / FR-21 / S5 提示词 11.2）。

        启动期 fail-fast 的三条红线：
        1. provider 取值必须在白名单内；
        2. 候选预算 1–3（含初始候选）——越界直接拒绝启动，避免运行期突破 FR-21 硬上限；
        3. mock provider 仅限非生产：生产环境用固定响应等于关闭生成能力（11.2「生产拒绝启用」）。
        真实 Provider 还要求 API Key / BASE_URL / MODEL 齐备，否则请求必然失败。
        """
        if self.llm_provider not in LLM_PROVIDERS:
            raise ProductionConfigError(
                f"ETL_AGENT_LLM_PROVIDER 非法: {self.llm_provider!r}，可选 {sorted(LLM_PROVIDERS)}"
            )
        if not 1 <= self.llm_max_candidates <= LLM_MAX_CANDIDATES_HARD_CAP:
            raise ProductionConfigError(
                f"ETL_AGENT_LLM_MAX_CANDIDATES 必须为 1–{LLM_MAX_CANDIDATES_HARD_CAP}（含初始候选，FR-21 硬上限），"
                f"当前为 {self.llm_max_candidates}"
            )
        if self.llm_provider == "mock":
            if self.is_production:
                raise ProductionConfigError(
                    "ENV=prod 禁止 ETL_AGENT_LLM_PROVIDER=mock（S5 提示词 11.2：mock 仅测试用）"
                )
            return
        missing = [
            name
            for name, value in (
                ("ETL_AGENT_LLM_API_KEY", self.llm_api_key),
                ("ETL_AGENT_LLM_BASE_URL", self.llm_base_url),
                ("ETL_AGENT_LLM_MODEL", self.llm_model),
            )
            if not value.strip()
        ]
        if missing:
            raise ProductionConfigError(
                f"ETL_AGENT_LLM_PROVIDER={self.llm_provider} 时 {' / '.join(missing)} 必填（FR-18）"
            )
        if self.llm_timeout_seconds <= 0:
            raise ProductionConfigError(
                f"ETL_AGENT_LLM_TIMEOUT_SECONDS 必须为正数，当前为 {self.llm_timeout_seconds}"
            )

    def _validate_fernet_key(self) -> None:
        """校验 Fernet key 格式（base64 编码的 32 字节）。"""
        import base64
        import binascii

        if not self.fernet_key.strip():
            raise ProductionConfigError(
                "ETL_AGENT_FERNET_KEY 未配置（FR-06 / S3 禁止事项 18：禁止硬编码，必须从环境变量读取）"
            )
        try:
            raw = base64.urlsafe_b64decode(self.fernet_key.encode("utf-8"))
        except (binascii.Error, ValueError) as exc:
            raise ProductionConfigError("ETL_AGENT_FERNET_KEY 不是合法 base64") from exc
        if len(raw) != FERNET_KEY_BYTES:
            raise ProductionConfigError(
                f"ETL_AGENT_FERNET_KEY 解码后须为 {FERNET_KEY_BYTES} 字节，实际 {len(raw)}"
            )

    @property
    def fernet_previous_keys_list(self) -> list[str]:
        """历史主密钥列表（逗号分隔，空项过滤）。"""
        return [item.strip() for item in self.fernet_previous_keys.split(",") if item.strip()]


settings = Settings()
