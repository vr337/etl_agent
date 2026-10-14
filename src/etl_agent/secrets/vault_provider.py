"""M-C 凭据治理 — vault-kv-v2 模式（hvac 客户端 + 定位引用）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / ADR-05 / S-Q7（Q5）
覆盖 FR：FR-06（vault-kv-v2：外部托管，库内仅存不可认证的定位引用）
对应 AC：AC-02（库内无任何秘密材料，拖库亦无法还原凭据）

Q5/S-Q7 待定：本阶段实现接口 + 连通性；Vault 生产部署形态留待决议，
因此未配置 VAULT_ADDR 时构造即失败（明确错误，不静默降级到 fernet-db）。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

import hvac
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.config import Settings
from etl_agent.infrastructure.models import EncryptedSecret
from etl_agent.secrets.errors import SecretDestroyed, SecretNotFound, VaultUnavailable
from etl_agent.secrets.provider import SecretRef

PROVIDER_NAME = "vault-kv-v2"


class VaultSecretProvider:
    """vault-kv-v2 实现：秘密存 Vault，库内只留 vault_path/vault_mount 定位引用（ADR-05）。"""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        if not settings.vault_addr.strip():
            # 失败关闭：不允许在未配置 Vault 时假装可用（Q5 待定，明确失败）
            raise VaultUnavailable("ETL_AGENT_VAULT_ADDR 未配置（Q5/S-Q7 待定）")
        self._session = session
        self._settings = settings
        self._client = hvac.Client(url=settings.vault_addr, token=settings.vault_token or None)

    async def store(
        self, plaintext: dict[str, str], *, secret_id: uuid.UUID | None = None
    ) -> SecretRef:
        """写入 Vault KV v2，库内行只存定位引用（ciphertext 恒为 NULL，AC-02）。

        secret_id 由调用方指定时（Provider 迁移冻结目标 UUID，FR-08）沿用该 ID，
        Vault 路径随之稳定——重试写同一路径，不产生孤立秘密。
        """
        target_id = secret_id or uuid.uuid4()
        path = self._path_for(target_id)
        await asyncio.to_thread(
            self._client.secrets.kv.v2.create_or_update_secret,
            path=path,
            secret=plaintext,
            mount_point=self._settings.vault_mount,
        )
        secret = EncryptedSecret(
            id=target_id,
            provider=PROVIDER_NAME,
            vault_mount=self._settings.vault_mount,
            vault_path=path,
        )
        self._session.add(secret)
        await self._session.flush()
        return SecretRef(secret_id=secret.id, provider=PROVIDER_NAME)

    async def read(self, secret_id: uuid.UUID) -> dict[str, str]:
        """从 Vault 拉取（定位引用不含秘密，读取才产生内存中短暂持有）。"""
        secret = await self._load(secret_id)
        if secret.destroyed_at is not None:
            raise SecretDestroyed("凭据已销毁（墓碑）")
        assert secret.vault_path is not None  # vault 模式必有定位引用
        response = await asyncio.to_thread(
            self._client.secrets.kv.v2.read_secret_version,
            path=secret.vault_path,
            mount_point=secret.vault_mount or self._settings.vault_mount,
        )
        data = response["data"]["data"]
        return {str(key): str(value) for key, value in data.items()}

    async def destroy(self, secret_id: uuid.UUID) -> None:
        """删除 Vault 密钥材料 + 库内墓碑（定位引用一并清除，不含秘密）。"""
        secret = await self._load(secret_id)
        if secret.vault_path is not None:
            await asyncio.to_thread(
                self._client.secrets.kv.v2.delete_metadata_and_all_versions,
                path=secret.vault_path,
                mount_point=secret.vault_mount or self._settings.vault_mount,
            )
        secret.vault_path = None
        secret.vault_mount = None
        secret.destroyed_at = datetime.now(UTC)
        await self._session.flush()

    async def rotate(self, secret_id: uuid.UUID, plaintext: dict[str, str]) -> SecretRef:
        """轮换暂存：写新 Vault 路径 + 新定位引用，旧路径由销毁流程清理。"""
        await self._load(secret_id)
        return await self.store(plaintext)

    def _path_for(self, secret_id: uuid.UUID) -> str:
        """Vault 路径（按 secret_id 稳定寻址，重试写同一路径避免孤立秘密，FR-08 同思路）。"""
        return f"etl-agent/{secret_id}"

    async def _load(self, secret_id: uuid.UUID) -> EncryptedSecret:
        result = await self._session.execute(
            select(EncryptedSecret).where(EncryptedSecret.id == secret_id)
        )
        secret = result.scalar_one_or_none()
        if secret is None:
            raise SecretNotFound(f"Secret 引用不存在: {secret_id}")
        return secret


def describe_config(settings: Settings) -> dict[str, str]:
    """脱敏的 Provider 配置状态（/health 与排查用；绝不回传 token，PRD 10.5）。"""
    return {
        "provider": settings.secret_provider,
        "vault_addr_configured": "true" if settings.vault_addr.strip() else "false",
        "fernet_key_version": str(settings.fernet_key_version),
    }


def parse_plaintext(raw: str) -> dict[str, str]:
    """Vault 读出的 JSON 字符串解析（辅助测试与 CLI）。"""
    data: dict[str, str] = json.loads(raw)
    return data
