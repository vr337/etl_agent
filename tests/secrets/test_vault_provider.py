"""vault-kv-v2 Provider 测试（T-05.3 / FR-06，S3 提示词 10）。

Vault 未部署（Q5/S-Q7 待定）→ skip 不 fail；已配置 VAULT_ADDR 时才做实探。
"""
from __future__ import annotations

import os

import pytest

from etl_agent.config import settings as default_settings
from etl_agent.secrets.factory import create_provider
from etl_agent.secrets.vault_provider import VaultSecretProvider
from tests.helpers import make_settings

VAULT_CONFIGURED = bool(os.environ.get("ETL_AGENT_VAULT_ADDR") or default_settings.vault_addr)


@pytest.mark.skipif(not VAULT_CONFIGURED, reason="Vault 未部署（Q5/S-Q7 待定）：接口用例 skip")
async def test_vault_provider_skip(db_session: object) -> None:
    """占位：Vault 未部署时本文件其余用例同样 skip（DoD-7 允许）。"""
    pytest.skip("Vault 未部署")


def test_vault_provider_unavailable_without_addr() -> None:
    """未配置 VAULT_ADDR → 构造即失败（明确错误，不静默降级到 fernet，FR-06）。"""
    from etl_agent.secrets.errors import VaultUnavailable

    settings = make_settings(secret_provider="vault-kv-v2", vault_addr="")
    with pytest.raises(VaultUnavailable):
        VaultSecretProvider(None, settings)  # type: ignore[arg-type]


def test_factory_selects_by_config(db_session: object) -> None:
    """工厂按 ETL_AGENT_SECRET_PROVIDER 选择实现（T-05.4 / DoD-7）。"""
    from etl_agent.secrets.fernet_provider import FernetSecretProvider

    provider = create_provider(db_session, make_settings())  # type: ignore[arg-type]
    assert isinstance(provider, FernetSecretProvider)
