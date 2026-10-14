"""M-C 凭据治理 — Provider 工厂（按 ETL_AGENT_SECRET_PROVIDER 选择）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / ADR-05
覆盖 FR：FR-06（SecretProvider 双模式）
对应 AC：AC-02（ Provider 选择不影响零明文保证）
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.config import Settings
from etl_agent.config import settings as default_settings
from etl_agent.secrets.fernet_provider import FernetSecretProvider
from etl_agent.secrets.provider import SecretProvider
from etl_agent.secrets.vault_provider import VaultSecretProvider


def create_provider(session: AsyncSession, settings: Settings | None = None) -> SecretProvider:
    """按配置构造 SecretProvider（T-05.4）。

    Args:
        session: 请求级数据库会话（Provider 为请求级实例，不跨请求共享）。
        settings: 应用配置；缺省用 config.settings。

    Returns:
        SecretProvider: fernet-db 或 vault-kv-v2 实现。

    Raises:
        ProductionConfigError: provider 取值非法（启动期 validate_secrets 亦会拦截）。
        VaultUnavailable:      vault-kv-v2 模式但 VAULT_ADDR 未配置。
    """
    effective = settings or default_settings
    if effective.secret_provider == "vault-kv-v2":
        return VaultSecretProvider(session, effective)
    if effective.secret_provider == "fernet-db":
        return FernetSecretProvider(session, effective)
    from etl_agent.config import ProductionConfigError

    raise ProductionConfigError(
        f"ETL_AGENT_SECRET_PROVIDER 非法: {effective.secret_provider!r}"
    )
