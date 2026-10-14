"""M-A/M-C/M-D/M-E/M-F 横切配置 — 应用配置加载、生产启动强制校验、SecretProvider / 文件资产 / LLM Provider 配置。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-A/M-C/M-D/M-E/M-F / 第 4.2 章环境差异 / 第 4.4 章初始化与配置要点 / 第 8 章「性能 · LLM 生成」
覆盖 FR：FR-58/65（认证双模式）、FR-01（Bootstrap Token）、FR-06（SecretProvider 双模式）、FR-12（采样上限）、FR-13（文件资产上限与挂载根目录）、FR-18（LLM Provider 选择）、FR-21（候选预算硬上限）
对应 AC：AC-11（生产缺失配置拒绝启动）、AC-02（凭据零明文：密钥只来自环境变量）、AC-04（有限修复预算 ≤3）
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

# 有限修复候选预算硬上限（SPEC FR-21 / S5 禁止事项 15）：含初始候选最多 3 份
LLM_MAX_CANDIDATES_HARD_CAP = 3

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
