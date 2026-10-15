"""M-N 审计证据 — 不可篡改证据账本（只追加事件流 + 哈希链 + 整流验证）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N / 第 5.2 章 harness_evidence_streams /
harness_evidence_events / 第 8 章「安全 · 审计」
覆盖 FR：FR-36（sequence / payload_sha256 / previous_hash / event_hash / 可选 Ed25519 检查点签名）
对应 AC：AC-09（Benchmark 证据覆盖率门禁）、AC-02（正文只以摘要入链）

模块职责：证据流（`stream`）、事件追加（`append`）、哈希链（`chain`）、
检查点签名（`checkpoint`）、整流验证（`verify`）、API（`router` / `schemas`）。
**边界**：不修改 / 不删除既有事件（SPEC 3.2 M-N 红线）。
"""

from etl_agent.evidence.append import AppendedEvent, EvidenceEventInput, append_event, append_events
from etl_agent.evidence.chain import (
    CHAIN_VERSION,
    GENESIS_HASH,
    compute_event_hash,
    payload_digest,
    verify_link,
)
from etl_agent.evidence.checkpoint import CheckpointSigner, checkpoint_payload, verify_checkpoint
from etl_agent.evidence.errors import (
    EvidenceChainBroken,
    EvidenceStreamNotFound,
    EvidenceVerifyFailed,
)
from etl_agent.evidence.stream import get_or_create_stream, require_stream
from etl_agent.evidence.verify import VerificationReport, verify_stream

__all__ = [
    "CHAIN_VERSION",
    "GENESIS_HASH",
    "AppendedEvent",
    "CheckpointSigner",
    "EvidenceChainBroken",
    "EvidenceEventInput",
    "EvidenceStreamNotFound",
    "EvidenceVerifyFailed",
    "VerificationReport",
    "append_event",
    "append_events",
    "checkpoint_payload",
    "compute_event_hash",
    "get_or_create_stream",
    "payload_digest",
    "require_stream",
    "verify_checkpoint",
    "verify_link",
    "verify_stream",
]
