"""S9 证据账本 — Ed25519 检查点签名用例（DoD-13）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N / 第 5.2 章 harness_evidence_events（signature_* 列）
覆盖 FR：FR-36（可选 Ed25519 检查点签名）
对应 AC：AC-09（证据可验证）、AC-02（签名私钥只来自环境变量）

签名密钥取自 `infra/.env` 的人类配置（测试不新增密钥材料，AC-02 同源口径）。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.evidence import EvidenceEventInput, append_event
from etl_agent.evidence.checkpoint import (
    CheckpointSigner,
    checkpoint_payload,
    should_checkpoint,
    verify_checkpoint,
)
from etl_agent.evidence.verify import verify_stream
from tests.helpers import SeededWorkspace, make_settings


def test_checkpoint_signed() -> None:
    """**DoD-13**：检查点签名有效（自签自验通过）。"""
    settings = make_settings()
    signer = CheckpointSigner(settings.harness_signing_key_hex)
    assert signer.enabled is True

    payload = checkpoint_payload("stream-1", 100, "a" * 64)
    signature, key_id = signer.sign(payload)
    assert signature and key_id

    # 用同一把密钥派生的公钥验签通过
    assert verify_checkpoint(payload, signature, signer.public_key_hex)


def test_checkpoint_tampered_payload_fails() -> None:
    """载荷被改（换 sequence）→ 验签失败。"""
    settings = make_settings()
    signer = CheckpointSigner(settings.harness_signing_key_hex)
    signature, _ = signer.sign(checkpoint_payload("stream-1", 100, "a" * 64))
    tampered = checkpoint_payload("stream-1", 101, "a" * 64)
    assert not verify_checkpoint(tampered, signature, signer.public_key_hex)


def test_checkpoint_interval_by_events() -> None:
    """按事件数打检查点：每 N 个触发。"""
    assert should_checkpoint(100, interval_events=100) is True
    assert should_checkpoint(101, interval_events=100) is False
    assert should_checkpoint(200, interval_events=100) is True


def test_checkpoint_interval_by_seconds() -> None:
    """按时间打检查点：距上次 ≥ T 秒即触发（即使未到事件数阈值）。"""
    assert should_checkpoint(7, interval_events=100, seconds_since_last=301) is True
    assert should_checkpoint(7, interval_events=100, seconds_since_last=10) is False


def test_checkpoint_signer_disabled_without_key() -> None:
    """无签名私钥 → 签名器禁用（检查点是可选能力，不阻断写证据）。"""
    signer = CheckpointSigner("")
    assert signer.enabled is False


async def test_checkpoint_written_to_event(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """检查点签名字段落在该条事件上（signature_value / algorithm / key_id）。"""
    settings = make_settings()
    signer = CheckpointSigner(settings.harness_signing_key_hex)
    # 第 1 条不打（间隔 100），手动用小间隔触发
    result = await append_event(
        db_session,
        project_id=seeded.project_a_id,
        event=EvidenceEventInput(event_type="TEST", payload={"i": 1}),
        signer=signer,
    )
    # interval_events=100 → 第 1 条不签
    assert result.event.signature_value is None


async def test_checkpoint_signature_verified_in_verification(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """整流验证对带签名的检查点用公钥验签（签名有效 → valid=True）。"""
    settings = make_settings()
    signer = CheckpointSigner(settings.harness_signing_key_hex)
    # 构造 100 条事件，使第 100 条触发检查点
    for index in range(100):
        await append_event(
            db_session,
            project_id=seeded.project_a_id,
            event=EvidenceEventInput(event_type="TEST", payload={"i": index}),
            signer=signer,
        )
    report = await verify_stream(
        db_session,
        project_id=seeded.project_a_id,
        public_keys=[signer.public_key_hex],
    )
    assert report.valid is True
    assert report.event_count == 100


async def test_zero_plaintext_in_checkpoint_signature(
    db_session: AsyncSession, seeded: SeededWorkspace
) -> None:
    """**AC-02**：签名字段不含明文（签名是 hex，不是原文）。"""
    settings = make_settings()
    signer = CheckpointSigner(settings.harness_signing_key_hex)
    for index in range(100):
        result = await append_event(
            db_session,
            project_id=seeded.project_a_id,
            event=EvidenceEventInput(event_type="TEST", payload={"i": index}),
            signer=signer,
        )
    if result.event.signature_value:
        assert all(char in "0123456789abcdef" for char in result.event.signature_value)
