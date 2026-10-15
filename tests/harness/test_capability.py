"""Capability 签发 / 验签测试（T-12.8；S6 提示词第十二节 tests/harness）。

覆盖（FR-33）：TTL ≤900s、claims 绑定、签名验签、过期拒绝、绑定漂移拒绝。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from etl_agent.harness.capability.issuer import (
    CapabilityClaims,
    CapabilityIssuer,
    resolve_ttl,
)
from etl_agent.harness.capability.verifier import CapabilityVerifier
from etl_agent.harness.errors import CapabilityExpired, CapabilityInvalid
from etl_agent.harness.policy.package import PolicyPackageInvalid

NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)


def _claims(**overrides: object) -> CapabilityClaims:
    defaults: dict[str, object] = {
        "tool_name": "pipeline.execute",
        "tool_version": "1.0.0",
        "subject": str(uuid.uuid4()),
        "environment": "prod",
        "artifact_sha256": "a" * 64,
        "supervision_contract_sha256": "b" * 64,
        "classification_report_sha256": "c" * 64,
        "allowed_mitigations": ["CANCEL", "PAUSE"],
        "project_id": str(uuid.uuid4()),
        "preparation_id": str(uuid.uuid4()),
        "pipeline_version_id": str(uuid.uuid4()),
    }
    defaults.update(overrides)
    return CapabilityClaims(**defaults)  # type: ignore[arg-type]


def _issue(signing_key: str, **overrides: object) -> object:
    issuer = CapabilityIssuer(signing_key)
    return issuer.issue(_claims(**overrides), now=NOW)


async def test_capability_claims_binding(harness_signing_key, harness_public_keys) -> None:
    """claims 绑定工具/版本/主体/环境/制品指纹/监督契约哈希/分级报告哈希/减损动作（FR-33）。"""
    capability = _issue(harness_signing_key)
    claims = capability.claims  # type: ignore[attr-defined]
    for field in (
        "tool_name",
        "tool_version",
        "subject",
        "environment",
        "artifact_sha256",
        "supervision_contract_sha256",
        "classification_report_sha256",
        "allowed_mitigations",
        "project_id",
        "preparation_id",
        "pipeline_version_id",
    ):
        assert field in claims


async def test_capability_ttl_bounded(harness_signing_key) -> None:
    """TTL 默认 300s；越界（30–900 之外）被夹紧到边界（FR-33 / S6 禁止事项 17）。"""
    assert resolve_ttl(None) == 300
    assert resolve_ttl(1) == 30
    assert resolve_ttl(99999) == 900

    capability = _issue(harness_signing_key)
    lifetime = capability.expires_at - NOW  # type: ignore[attr-defined]
    assert lifetime == timedelta(seconds=300)
    assert lifetime <= timedelta(seconds=900)


async def test_capability_verify_and_reject_tampered_token(
    harness_signing_key, harness_public_keys
) -> None:
    """验签通过；令牌正文被改 → HARNESS_401_CAPABILITY_INVALID（FR-33）。"""
    capability = _issue(harness_signing_key)
    verifier = CapabilityVerifier(harness_public_keys)
    claims = verifier.verify(capability.token, now=NOW)  # type: ignore[attr-defined]
    assert claims["tool_name"] == "pipeline.execute"

    tampered = capability.token[:-4] + ("0000" if capability.token[-4:] != "0000" else "1111")  # type: ignore[attr-defined]
    with pytest.raises(CapabilityInvalid):
        verifier.verify(tampered, now=NOW)


async def test_capability_expired(harness_signing_key, harness_public_keys) -> None:
    """过期令牌 → HARNESS_401_CAPABILITY_EXPIRED（SPEC 6.4）。"""
    capability = _issue(harness_signing_key)
    verifier = CapabilityVerifier(harness_public_keys)
    later = NOW + timedelta(seconds=301)
    with pytest.raises(CapabilityExpired):
        verifier.verify(capability.token, now=later)  # type: ignore[attr-defined]


async def test_capability_binding_mismatch(harness_signing_key, harness_public_keys) -> None:
    """claims 绑定漂移（换制品指纹 / 换环境）→ 拒绝（AC-05 令牌只对这一次调用有效）。"""
    capability = _issue(harness_signing_key)
    verifier = CapabilityVerifier(harness_public_keys)
    with pytest.raises(CapabilityInvalid) as excinfo:
        verifier.verify_against(
            capability.token,  # type: ignore[attr-defined]
            now=NOW,
            expected={"artifact_sha256": "z" * 64},
        )
    assert "artifact_sha256" in str(excinfo.value)


async def test_capability_sha256_of_token_only(harness_signing_key) -> None:
    """库中只存 `token_sha256`：令牌本体与摘要不同，且摘要 64-hex（FR-33）。"""
    from etl_agent.harness.capability.issuer import hash_token

    capability = _issue(harness_signing_key)
    digest = capability.token_sha256  # type: ignore[attr-defined]
    assert len(digest) == 64
    assert digest != capability.token  # type: ignore[attr-defined]
    assert hash_token(capability.token) == digest  # type: ignore[attr-defined]


def test_capability_issuer_rejects_bad_key() -> None:
    """私钥缺失 / 非 32 字节 → 拒绝（启动期失败，不产出不可验签的令牌）。"""
    with pytest.raises(PolicyPackageInvalid):
        CapabilityIssuer("")
    with pytest.raises(PolicyPackageInvalid):
        CapabilityIssuer("not-hex")
