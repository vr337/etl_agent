"""S9 安全进化 — 发布清单 Ed25519 签名用例（FR-52）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 5.2 章 safety_releases /
第 8 章「安全 · 授权与职责分离」
覆盖 FR：FR-52（发布清单 Ed25519 签名）
对应 AC：AC-09（安全进化可验证）、AC-02（私钥只来自环境变量）
"""

from __future__ import annotations

import pytest

from etl_agent.safety.errors import SafetySignatureInvalid
from etl_agent.safety.signing import (
    SIGNATURE_ALGORITHM,
    ReleaseManifest,
    ReleaseSigner,
    key_id,
    require_valid_release,
    verify_release,
)
from tests.helpers import make_settings

# 固定的合法 Ed25519 私钥（32 字节 hex）——测试专用，非生产材料
TEST_KEY_HEX = "0" * 63 + "1"
OTHER_KEY_HEX = "1" * 64


def _manifest(**overrides: object) -> ReleaseManifest:
    base = {
        "proposal_id": "p-1",
        "manifest_sha256": "a" * 64,
        "stage": "SHADOW_RUNNING",
        "canary_percent": None,
    }
    base.update(overrides)
    return ReleaseManifest(**base)  # type: ignore[arg-type]


def test_algorithm_is_ed25519() -> None:
    """签名算法为 Ed25519（SPEC FR-52）。"""
    assert SIGNATURE_ALGORITHM == "Ed25519"


def test_manifest_canonical_is_deterministic() -> None:
    """规范化字节串：同清单恒得同载荷（键排序 + 紧凑分隔）。"""
    a = _manifest().canonical()
    b = _manifest().canonical()
    assert a == b
    assert a.startswith(b"{")


def test_manifest_canonical_changes_with_any_field() -> None:
    """任一字段变化 → 载荷变化（换字段即验签失败）。"""
    base = _manifest().canonical()
    assert _manifest(canary_percent=5).canonical() != base
    assert _manifest(stage="CANARY").canonical() != base
    assert _manifest(manifest_sha256="b" * 64).canonical() != base
    assert _manifest(proposal_id="p-2").canonical() != base


def test_signer_key_id_stable() -> None:
    """同一私钥 → 同一 key_id（可辨识签名来源）。"""
    signer = ReleaseSigner(TEST_KEY_HEX)
    assert signer.key_id == signer.key_id
    assert signer.key_id == key_id(signer.public_key_hex)
    assert len(signer.key_id) == 16


def test_different_keys_have_different_ids() -> None:
    """不同私钥 → 不同 key_id。"""
    assert ReleaseSigner(TEST_KEY_HEX).key_id != ReleaseSigner(OTHER_KEY_HEX).key_id


def test_sign_and_verify_roundtrip() -> None:
    """签名 → 用对应公钥验签成功。"""
    signer = ReleaseSigner(TEST_KEY_HEX)
    manifest = _manifest()
    signature = signer.sign(manifest)
    assert verify_release(manifest, signature, signer.public_key_hex) is True


def test_verify_fails_with_wrong_key() -> None:
    """用不匹配的公钥验签 → False。"""
    signer = ReleaseSigner(TEST_KEY_HEX)
    other = ReleaseSigner(OTHER_KEY_HEX)
    signature = signer.sign(_manifest())
    assert verify_release(_manifest(), signature, other.public_key_hex) is False


def test_verify_fails_when_manifest_tampered() -> None:
    """清单被改（如偷改小流量比例）→ 验签失败。"""
    signer = ReleaseSigner(TEST_KEY_HEX)
    signature = signer.sign(_manifest(canary_percent=1))
    # 事后把 1% 改成 100%
    tampered = _manifest(canary_percent=100)
    assert verify_release(tampered, signature, signer.public_key_hex) is False


def test_verify_fails_on_malformed_signature() -> None:
    """畸形签名（非 hex / 长度错）→ False（不抛）。"""
    signer = ReleaseSigner(TEST_KEY_HEX)
    manifest = _manifest()
    assert verify_release(manifest, "not-hex", signer.public_key_hex) is False
    assert verify_release(manifest, "ab" * 4, signer.public_key_hex) is False


def test_verify_fails_on_malformed_public_key() -> None:
    """畸形公钥 → False（不抛）。"""
    signer = ReleaseSigner(TEST_KEY_HEX)
    assert verify_release(_manifest(), signer.sign(_manifest()), "zzzz") is False


def test_signer_rejects_empty_key() -> None:
    """**AC-02**：未配置私钥 → 拒绝构造签名器。"""
    with pytest.raises(SafetySignatureInvalid):
        ReleaseSigner("")


def test_signer_rejects_bad_key_length() -> None:
    """非法长度私钥 → 拒绝（不静默用错误的密钥签名）。"""
    with pytest.raises(SafetySignatureInvalid):
        ReleaseSigner("abcd")


def test_require_valid_release_accepts_matching_key() -> None:
    """发布前置校验：签名与任一公钥匹配即通过。"""
    signer = ReleaseSigner(TEST_KEY_HEX)
    manifest = _manifest()
    require_valid_release(manifest, signer.sign(manifest), [signer.public_key_hex])


def test_require_valid_release_accepts_among_many_keys() -> None:
    """多公钥轮换：命中其中之一即通过。"""
    signer = ReleaseSigner(TEST_KEY_HEX)
    manifest = _manifest()
    other = ReleaseSigner(OTHER_KEY_HEX).public_key_hex
    require_valid_release(
        manifest, signer.sign(manifest), [other, signer.public_key_hex]
    )


def test_require_valid_release_rejects_missing_signature() -> None:
    """签名缺失 → 拒绝（BIZ_422_SAFETY_SIGNATURE_INVALID）。"""
    with pytest.raises(SafetySignatureInvalid, match="缺少"):
        require_valid_release(_manifest(), None, [ReleaseSigner(TEST_KEY_HEX).public_key_hex])


def test_require_valid_release_rejects_no_public_keys() -> None:
    """未配置验签公钥 → 拒绝（无法校验即不放行）。"""
    signer = ReleaseSigner(TEST_KEY_HEX)
    with pytest.raises(SafetySignatureInvalid, match="未配置验签公钥"):
        require_valid_release(_manifest(), signer.sign(_manifest()), [])


def test_require_valid_release_rejects_mismatch() -> None:
    """签名与所有公钥都不匹配 → 拒绝（清单可能被篡改）。"""
    signer = ReleaseSigner(TEST_KEY_HEX)
    other = ReleaseSigner(OTHER_KEY_HEX)
    with pytest.raises(SafetySignatureInvalid, match="校验失败"):
        require_valid_release(
            _manifest(), signer.sign(_manifest()), [other.public_key_hex]
        )


def test_production_signer_uses_configured_key() -> None:
    """签名器私钥来自人类配置的 `harness_signing_key_hex`（AC-02：不新增密钥材料）。"""
    signer = ReleaseSigner(make_settings().harness_signing_key_hex)
    manifest = _manifest()
    assert verify_release(manifest, signer.sign(manifest), signer.public_key_hex)
