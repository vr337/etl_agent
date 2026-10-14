"""fernet-db Provider 测试（T-05.2 / FR-06，S3 提示词 10）。

覆盖：存取一致、销毁墓碑（read 抛异常）、key_version 轮换后旧密文仍可读。
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.secrets.errors import SecretDestroyed
from etl_agent.secrets.fernet_provider import FernetSecretProvider
from tests.helpers import make_fernet_key, make_settings


async def test_fernet_provider_store_read(db_session: AsyncSession) -> None:
    """存储 → 读取，明文一致（FR-06）。"""
    provider = FernetSecretProvider(db_session, make_settings())
    plaintext = {"username": "alice", "password": "s3cret-but-not-persisted"}
    ref = await provider.store(plaintext)
    assert ref.provider == "fernet-db"
    assert await provider.read(ref.secret_id) == plaintext


async def test_fernet_provider_destroy(db_session: AsyncSession) -> None:
    """销毁 → 墓碑；read 抛 SecretDestroyed（FR-06/09：墓碑不含秘密）。"""
    provider = FernetSecretProvider(db_session, make_settings())
    ref = await provider.store({"username": "bob", "password": "temp-value"})
    await provider.destroy(ref.secret_id)
    with pytest.raises(SecretDestroyed):
        await provider.read(ref.secret_id)


async def test_fernet_key_version_rotation(db_session: AsyncSession) -> None:
    """主密钥轮换：新密文带新 key_version，旧密文经 MultiFernet 仍可读（FR-06）。"""
    old_key = make_fernet_key()
    new_key = make_fernet_key()
    old_settings = make_settings(fernet_key=old_key, fernet_key_version=1)
    new_settings = make_settings(
        fernet_key=new_key, fernet_key_version=2, fernet_previous_keys=old_key
    )

    old_provider = FernetSecretProvider(db_session, old_settings)
    ref = await old_provider.store({"username": "carol", "password": "old-cipher"})

    new_provider = FernetSecretProvider(db_session, new_settings)
    # 旧密文在新密钥下仍可读（MultiFernet 解密集合含历史密钥）
    assert await new_provider.read(ref.secret_id) == {"username": "carol", "password": "old-cipher"}
    # 新存储使用新版本号
    new_ref = await new_provider.store({"username": "carol", "password": "new-cipher"})
    new_read = await new_provider.read(new_ref.secret_id)
    assert new_read["password"] == "new-cipher"
    assert new_ref.secret_id != ref.secret_id


async def test_fernet_provider_rotate_stages_new(db_session: AsyncSession) -> None:
    """rotate：写新 Secret 行，旧行不动（FR-07 暂存语义）。"""
    provider = FernetSecretProvider(db_session, make_settings())
    old_ref = await provider.store({"username": "dave", "password": "old"})
    new_ref = await provider.rotate(old_ref.secret_id, {"username": "dave", "password": "new"})
    assert new_ref.secret_id != old_ref.secret_id
    old_read = await provider.read(old_ref.secret_id)
    new_read = await provider.read(new_ref.secret_id)
    assert old_read["password"] == "old"
    assert new_read["password"] == "new"
