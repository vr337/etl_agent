"""M-C 凭据治理 — fernet-db 模式（cryptography.Fernet + key_version）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / ADR-05
覆盖 FR：FR-06（fernet-db：本地密文 + key_version 管理）
对应 AC：AC-02（密文落库、明文只在内存中短暂持有）

密文格式：Fernet token（base64url，自带 HMAC 完整性校验）。
key_version 管理：主密钥轮换时新密文带新版本号，历史密钥进入 MultiFernet
解密集合——旧密文保持可读（FR-06），直至引用全部迁移后历史密钥才可下线。
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.config import Settings
from etl_agent.infrastructure.models import EncryptedSecret
from etl_agent.secrets.errors import SecretDestroyed, SecretNotFound
from etl_agent.secrets.provider import SecretRef

PROVIDER_NAME = "fernet-db"


class FernetSecretProvider:
    """fernet-db 实现：密文 + key_version 落 encrypted_secrets，墓碑清空密钥材料。"""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        # Fernet 接受 base64 编码字符串（不接受解码后的原始字节）
        # 解密集合 = 当前密钥 + 历史密钥（key_version 轮换后旧密文仍可读，FR-06）
        fernets = [Fernet(settings.fernet_key)]
        fernets.extend(Fernet(key) for key in settings.fernet_previous_keys_list)
        self._fernet = MultiFernet(fernets)
        self._key_version = settings.fernet_key_version

    async def store(
        self, plaintext: dict[str, str], *, secret_id: uuid.UUID | None = None
    ) -> SecretRef:
        """JSON 序列化后加密落库（字段名与值均进入密文，不单独落库）。

        secret_id 由调用方指定时（Provider 迁移冻结目标 UUID，FR-08）沿用该 ID，
        保证重试写同一行、不产生孤立秘密。
        """
        token = self._fernet.encrypt(json.dumps(plaintext, ensure_ascii=False).encode("utf-8"))
        secret = EncryptedSecret(
            id=secret_id or uuid.uuid4(),
            provider=PROVIDER_NAME,
            ciphertext=token.decode("utf-8"),
            key_version=self._key_version,
        )
        self._session.add(secret)
        await self._session.flush()
        return SecretRef(secret_id=secret.id, provider=PROVIDER_NAME)

    async def read(self, secret_id: uuid.UUID) -> dict[str, str]:
        """解密读取；墓碑行（destroyed_at 非空）拒绝读取（FR-06）。"""
        secret = await self._load(secret_id)
        if secret.destroyed_at is not None:
            raise SecretDestroyed("凭据已销毁（墓碑）")
        assert secret.ciphertext is not None  # fernet 模式必有密文；vault 模式不会走到这里
        payload: dict[str, str] = json.loads(self._fernet.decrypt(secret.ciphertext.encode("utf-8")))
        return payload

    async def destroy(self, secret_id: uuid.UUID) -> None:
        """销毁：清空密文与 key_version，仅留 id/provider/时间戳墓碑（不含秘密，FR-06）。"""
        secret = await self._load(secret_id)
        secret.ciphertext = None
        secret.key_version = None
        secret.vault_path = None
        secret.vault_mount = None
        secret.destroyed_at = datetime.now(UTC)
        await self._session.flush()

    async def rotate(self, secret_id: uuid.UUID, plaintext: dict[str, str]) -> SecretRef:
        """轮换暂存：写新 Secret 行（新密文 + 当前 key_version），旧行不动。

        旧行的销毁资格由 rotation/destruction 状态机判定（FR-07/09），Provider 不自行删除。
        """
        await self._load(secret_id)  # 确认源引用存在（快速失败）
        return await self.store(plaintext)

    async def _load(self, secret_id: uuid.UUID) -> EncryptedSecret:
        result = await self._session.execute(
            select(EncryptedSecret).where(EncryptedSecret.id == secret_id)
        )
        secret = result.scalar_one_or_none()
        if secret is None:
            raise SecretNotFound(f"Secret 引用不存在: {secret_id}")
        return secret
