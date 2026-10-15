"""签名策略包测试（T-12.3；S6 提示词第十二节 tests/harness）。

覆盖（FR-30）：Ed25519 验签通过；篡改（清单 / 签名）→ 拒绝加载；rule_id/version 唯一。
"""
from __future__ import annotations

import json

import pytest

from etl_agent.harness.policy.package import (
    DEFAULT_PACKAGE_VERSION,
    PolicyPackageInvalid,
    build_package,
    compute_package_sha256,
    load_package,
    sign_package,
)


@pytest.fixture
def harness_package_paths() -> tuple[object, object]:
    """（json, sig）路径对：仓库交付的策略包（由 scripts/gen_policy_package.py 生成）。"""
    from pathlib import Path

    from etl_agent.config import settings

    return Path(settings.policy_package_path), Path(settings.policy_package_signature_path)


async def test_policy_package_verify(harness_package_paths, harness_public_keys) -> None:
    """交付策略包加载 + 验签通过，9 条规则与 5 个主机白名单齐备（FR-30）。"""
    json_path, sig_path = harness_package_paths
    package = load_package(str(json_path), str(sig_path), harness_public_keys)
    assert package.version == DEFAULT_PACKAGE_VERSION
    assert len(package.rules) == 9
    assert len(package.host_whitelist) == 5
    assert package.rule("HARNESS-P0-CROSS-PROJECT") is not None
    assert package.rule("HARNESS-P9-NOT-EXIST") is None


async def test_policy_package_tamper_manifest(
    harness_package_paths, harness_public_keys, tampered_package_path
) -> None:
    """篡改清单内容（签名不动）→ 摘要不符，拒绝加载（FR-30）。"""
    _, sig_path = harness_package_paths
    with pytest.raises(PolicyPackageInvalid) as excinfo:
        load_package(str(tampered_package_path), str(sig_path), harness_public_keys)
    assert "摘要不符" in str(excinfo.value)


async def test_policy_package_tamper_signature(
    harness_package_paths, harness_public_keys, tmp_path
) -> None:
    """篡改签名（hex 改一个字符）→ 验签失败，拒绝加载（FR-30）。"""
    json_path, sig_path = harness_package_paths
    forged = tmp_path / "forged.sig"
    original = sig_path.read_text(encoding="utf-8").strip()
    flipped = ("0" if original[0] != "0" else "1") + original[1:]
    forged.write_text(flipped + "\n", encoding="utf-8")
    with pytest.raises(PolicyPackageInvalid):
        load_package(str(json_path), str(forged), harness_public_keys)


async def test_policy_package_wrong_key(harness_package_paths) -> None:
    """用另一把 Ed25519 公钥验签 → 失败（防止用错密钥集放行）。"""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    other = Ed25519PrivateKey.generate()
    other_public = other.public_key().public_bytes_raw().hex()
    json_path, sig_path = harness_package_paths
    with pytest.raises(PolicyPackageInvalid):
        load_package(str(json_path), str(sig_path), [other_public])


async def test_policy_package_no_public_keys(harness_package_paths) -> None:
    """未配置公钥 → 拒绝（失败关闭，不降级放行）。"""
    json_path, sig_path = harness_package_paths
    with pytest.raises(PolicyPackageInvalid):
        load_package(str(json_path), str(sig_path), [])


async def test_policy_package_missing_file(tmp_path) -> None:
    """策略包文件缺失 → 拒绝（提示先运行生成脚本）。"""
    with pytest.raises(PolicyPackageInvalid) as excinfo:
        load_package(str(tmp_path / "nope.json"), str(tmp_path / "nope.sig"), ["a" * 64])
    assert "gen_policy_package" in str(excinfo.value)


async def test_policy_package_rule_id_version_unique() -> None:
    """重复 (rule_id, version) → 拒绝构造（FR-30 清单唯一性）。"""
    from etl_agent.harness.policy.package import PolicyRule

    rules = [
        PolicyRule(rule_id="HARNESS-P3-ROUTINE", version="1", enabled=True, params={}),
        PolicyRule(rule_id="HARNESS-P3-ROUTINE", version="1", enabled=False, params={}),
    ]
    with pytest.raises(PolicyPackageInvalid):
        build_package(rules=rules)


async def test_policy_package_sha256_excludes_itself() -> None:
    """`package_sha256` 不含自身字段（FR-30 口径：可复现摘要）。"""
    package = build_package()
    declared = package["package_sha256"]
    without = {key: value for key, value in package.items() if key != "package_sha256"}
    assert compute_package_sha256(without) == declared


async def test_policy_package_sign_roundtrip(harness_public_keys, harness_signing_key) -> None:
    """签名 → 验签回环成功；签对象改动一个字符即验签失败。"""
    package = build_package()
    signature = sign_package(package, harness_signing_key)
    tampered = json.loads(json.dumps(package))
    tampered["host_whitelist"] = ["evil.example.com"]

    from etl_agent.harness.policy.package import verify_package

    assert verify_package(package, signature, harness_public_keys) == package["package_sha256"]
    with pytest.raises(PolicyPackageInvalid):
        verify_package(tampered, signature, harness_public_keys)
