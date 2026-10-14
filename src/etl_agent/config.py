"""M-A/M-C 横切配置 — 应用配置加载、生产启动强制校验与 SecretProvider 配置。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-A/M-C / 第 4.2 章环境差异 / 第 4.4 章初始化与配置要点
覆盖 FR：FR-58/65（认证双模式）、FR-01（Bootstrap Token）、FR-06（SecretProvider 双模式）
对应 AC：AC-11（生产缺失配置拒绝启动）、AC-02（凭据零明文：密钥只来自环境变量）
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from etl_agent.infrastructure.database import settings as infra_settings

AUTH_MODES = frozenset({"dev-header", "trusted-gateway"})
ENVIRONMENTS = frozenset({"dev", "staging", "prod"})
SECRET_PROVIDERS = frozenset({"fernet-db", "vault-kv-v2"})

# 生产强制项（AC-11 / SPEC 4.4-3）：HMAC 密钥与 Bootstrap Token 的最小长度
MIN_SECRET_LENGTH = 32

# Fernet key 的 base64 解码后固定 32 字节（cryptography.fernet 规范）
FERNET_KEY_BYTES = 32


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

    # ---- 直连覆盖（.env.example 形态，优先级高于连接件拼装）----
    database_url: str | None = None
    checkpoint_database_url: str | None = None
    redis_url: str | None = None

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

    def validate_bootstrap(self) -> None:
        """Bootstrap 配置校验（FR-01）：启用时 Token 必须 ≥32 字节。

        启动期fail-fast，避免运行到首次初始化才发现 Token 不合规。
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
