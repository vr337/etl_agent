"""M-N 审计证据 — Ed25519 检查点签名（可选，按检查点间隔）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N / 第 5.2 章 harness_evidence_events（signature_* 列）
覆盖 FR：FR-36（可选 Ed25519 检查点签名）
对应 AC：AC-09（证据可验证）、AC-02（密钥只来自环境变量）

**检查点语义**（S9 提示词 §8.1）：每 N 个事件（默认 100）或每 T 秒（默认 300s）对
`{stream_id, sequence, event_hash}` 签名，签名落在**该事件的** signature_* 列上。

检查点不是额外的表——签名直接附在触发检查点的那条事件上，避免新增模型（S9 不新增迁移）。
`previous_hash` 链保证前面的事件不可篡改；检查点保证**链头**在当时被可信方确认过。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# 检查点间隔默认值（S9 提示词 §8.1）：每 100 个事件或每 300 秒
CHECKPOINT_INTERVAL_EVENTS = 100
CHECKPOINT_INTERVAL_SECONDS = 300

SIGNATURE_ALGORITHM = "Ed25519"


@dataclass(frozen=True)
class CheckpointPayload:
    """检查点签名载荷（三字段，与 S9 提示词 §8.1 一致）。"""

    stream_id: str
    sequence: int
    event_hash: str

    def canonical(self) -> bytes:
        """规范化字节串：键排序 + 紧凑分隔（同输入恒得同签名载荷）。"""
        return json.dumps(
            {
                "stream_id": self.stream_id,
                "sequence": self.sequence,
                "event_hash": self.event_hash,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")


def key_id(public_key_hex: str) -> str:
    """签名密钥标识：公钥 hex 的短摘要（多密钥轮换时定位用哪一把验签）。"""
    return hashlib.sha256(public_key_hex.encode("ascii")).hexdigest()[:16]


def should_checkpoint(
    sequence: int,
    *,
    interval_events: int = CHECKPOINT_INTERVAL_EVENTS,
    seconds_since_last: float | None = None,
    interval_seconds: int = CHECKPOINT_INTERVAL_SECONDS,
) -> bool:
    """判定当前事件是否应打检查点（按事件数或按时间，任一满足即打）。

    首事件（sequence=1）不强制定期检查点——间隔语义是「每 N 个」，不是「立刻」。
    """
    if interval_events > 0 and sequence % interval_events == 0:
        return True
    return seconds_since_last is not None and seconds_since_last >= interval_seconds


class CheckpointSigner:
    """检查点签名器（私钥来自 `ETL_AGENT_HARNESS_SIGNING_KEY_HEX`，与 Capability 同源）。"""

    def __init__(self, signing_key_hex: str) -> None:
        key_hex = signing_key_hex.strip()
        if not key_hex:
            # 无密钥时不签名：检查点是可选能力，缺失即降级为「无检查点」而非拒绝写证据
            self._private: Ed25519PrivateKey | None = None
            self._public_hex = ""
            return
        self._private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key_hex))
        self._public_hex = self._private.public_key().public_bytes_raw().hex()

    @property
    def enabled(self) -> bool:
        return self._private is not None

    @property
    def public_key_hex(self) -> str:
        """签名器对应公钥（hex）；未启用时为空串。"""
        return self._public_hex

    @property
    def key_id(self) -> str:
        return key_id(self._public_hex) if self._public_hex else ""

    def sign(self, payload: CheckpointPayload) -> tuple[str, str]:
        """签名检查点载荷。

        Returns:
            `(signature_hex, key_id)`；未启用签名时返回 `("", "")`。

        Raises:
            RuntimeError: 签名器未启用时调用（调用方须先判 `enabled`）。
        """
        if self._private is None:
            raise RuntimeError("检查点签名器未启用（未配置签名私钥）")
        return self._private.sign(payload.canonical()).hex(), self.key_id


def verify_checkpoint(
    payload: CheckpointPayload, signature_hex: str, public_key_hex: str
) -> bool:
    """用公钥验证检查点签名（整流验证期调用）。"""
    try:
        public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        public.verify(bytes.fromhex(signature_hex), payload.canonical())
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


def checkpoint_payload(stream_id: str, sequence: int, event_hash: str) -> CheckpointPayload:
    """构造检查点载荷（便于调用方与测试统一入口）。"""
    return CheckpointPayload(stream_id=stream_id, sequence=sequence, event_hash=event_hash)


__all__: list[str] = [
    "CHECKPOINT_INTERVAL_EVENTS",
    "CHECKPOINT_INTERVAL_SECONDS",
    "SIGNATURE_ALGORITHM",
    "CheckpointPayload",
    "CheckpointSigner",
    "checkpoint_payload",
    "key_id",
    "should_checkpoint",
    "verify_checkpoint",
]

# 类型别名导出（mypy 需要显式引用 Any 之外的公共类型）
PublicPayload = dict[str, Any]
