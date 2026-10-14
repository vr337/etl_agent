"""M-C 凭据治理 — SecretProvider 抽象接口。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / ADR-05（SecretProvider 双模式）
覆盖 FR：FR-06（受管加密存储：fernet-db / vault-kv-v2）
对应 AC：AC-02（全链路无敏感明文泄露）

接口约定（S3 提示词 9.1）：
- store/rotate 返回 SecretRef（只含 secret_id + provider），**不含任何密钥材料**；
- read 返回明文 dict，调用方只能内存中短暂持有，禁止写入日志/响应/数据库（禁止事项 16）；
- destroy 留墓碑（FR-06/09）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SecretRef:
    """Secret 引用：库内定位 + Provider 标识，绝不含密钥材料（AC-02）。"""

    secret_id: uuid.UUID
    provider: str


@runtime_checkable
class SecretProvider(Protocol):
    """凭据提供者协议（fernet-db / vault-kv-v2 双模式，ADR-05）。

    实现均为请求级实例（持有请求的数据库会话）——由 secrets.factory 按
    ETL_AGENT_SECRET_PROVIDER 选择，避免跨请求共享会话。
    """

    async def store(
        self, plaintext: dict[str, str], *, secret_id: uuid.UUID | None = None
    ) -> SecretRef:
        """加密存储明文凭据，返回引用。

        Args:
            plaintext: 明文凭据字段（如 {"username": ..., "password": ...}）。
            secret_id: 指定 Secret ID（Provider 迁移的冻结目标 UUID，FR-08）；
                缺省由实现分配新 UUID。

        Returns:
            SecretRef: 引用（secret_id + provider）。
        """
        ...

    async def read(self, secret_id: uuid.UUID) -> dict[str, str]:
        """读取并解密凭据（内存中短暂持有，禁止外泄，AC-02）。

        Raises:
            SecretNotFound:  引用不存在。
            SecretDestroyed: 已销毁（墓碑）。
        """
        ...

    async def destroy(self, secret_id: uuid.UUID) -> None:
        """销毁密钥材料并留墓碑（不含秘密，FR-06/09）。

        Raises:
            SecretNotFound: 引用不存在。
        """
        ...

    async def rotate(self, secret_id: uuid.UUID, plaintext: dict[str, str]) -> SecretRef:
        """暂存新凭据（轮换 STAGE 步骤，FR-07）：旧 Secret 行不动，返回新引用。"""
        ...
