"""M-A 认证鉴权 — 应用配置加载与生产启动强制校验。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-A / 第 4.2 章环境差异 / 第 4.4 章初始化与配置要点
覆盖 FR：FR-58（身份注入）、FR-65（认证双模式）、FR-01（Bootstrap Token）
对应 AC：AC-11（认证边界：生产缺失配置拒绝启动）
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from etl_agent.infrastructure.database import settings as infra_settings

AUTH_MODES = frozenset({"dev-header", "trusted-gateway"})
ENVIRONMENTS = frozenset({"dev", "staging", "prod"})

# 生产强制项（AC-11 / SPEC 4.4-3）：HMAC 密钥与 Bootstrap Token 的最小长度
MIN_SECRET_LENGTH = 32


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


settings = Settings()
