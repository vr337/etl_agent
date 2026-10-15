"""M-M 评测与安全进化 — 发布清单 Ed25519 签名（FR-52）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 5.2 章 safety_releases /
第 8 章「安全 · 授权与职责分离」
覆盖 FR：FR-52（发布清单 Ed25519 签名）
对应 AC：AC-09（安全进化可验证）、AC-02（私钥只来自环境变量）

签名载荷 `{proposal_id, manifest_sha256, stage, canary_percent}`：
换任何一项即验签失败，保证「批准的」与「发布的」是同一件事——
小流量比例被事后偷改会让「1% 试跑」变成「全量上线」，签名把这条路堵死。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from etl_agent.safety.errors import SafetySignatureInvalid

SIGNATURE_ALGORITHM = "Ed25519"


@dataclass(frozen=True)
class ReleaseManifest:
    """发布清单（被签名的治理事实）。"""

    proposal_id: str
    manifest_sha256: str
    stage: str
    canary_percent: int | None = None

    def canonical(self) -> bytes:
        """规范化字节串（键排序 + 紧凑分隔）：同清单恒得同签名载荷。"""
        return json.dumps(
            {
                "proposal_id": self.proposal_id,
                "manifest_sha256": self.manifest_sha256,
                "stage": self.stage,
                "canary_percent": self.canary_percent,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")


def key_id(public_key_hex: str) -> str:
    """签名密钥标识（公钥 hex 的短摘要）。"""
    return hashlib.sha256(public_key_hex.encode("ascii")).hexdigest()[:16]


class ReleaseSigner:
    """发布清单签名器（私钥来自 `ETL_AGENT_HARNESS_SIGNING_KEY_HEX`）。"""

    def __init__(self, signing_key_hex: str) -> None:
        key_hex = signing_key_hex.strip()
        if not key_hex:
            raise SafetySignatureInvalid("未配置发布签名私钥（FR-52 / AC-11）")
        try:
            self._private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key_hex))
        except (ValueError, TypeError) as exc:
            raise SafetySignatureInvalid("发布签名私钥不是合法 Ed25519 私钥（32 字节 hex）") from exc
        self._public_hex = self._private.public_key().public_bytes_raw().hex()

    @property
    def key_id(self) -> str:
        return key_id(self._public_hex)

    @property
    def public_key_hex(self) -> str:
        return self._public_hex

    def sign(self, manifest: ReleaseManifest) -> str:
        """签名发布清单，返回 signature hex。"""
        return self._private.sign(manifest.canonical()).hex()


def verify_release(
    manifest: ReleaseManifest, signature_hex: str, public_key_hex: str
) -> bool:
    """用公钥验证发布清单签名（发布前强制校验，失败即拒绝发布）。"""
    try:
        public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        public.verify(bytes.fromhex(signature_hex), manifest.canonical())
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


def require_valid_release(
    manifest: ReleaseManifest, signature_hex: str | None, public_keys: list[str]
) -> None:
    """发布前置校验：签名缺失或不匹配任一公钥即拒绝（`BIZ_422_SAFETY_SIGNATURE_INVALID`）。"""
    if not signature_hex:
        raise SafetySignatureInvalid("发布清单缺少 Ed25519 签名（FR-52）")
    if not public_keys:
        raise SafetySignatureInvalid("未配置验签公钥，无法校验发布清单（FR-52）")
    if not any(verify_release(manifest, signature_hex, key) for key in public_keys):
        raise SafetySignatureInvalid("发布清单签名校验失败（清单可能被篡改）")


__all__ = [
    "SIGNATURE_ALGORITHM",
    "ReleaseManifest",
    "ReleaseSigner",
    "key_id",
    "require_valid_release",
    "verify_release",
]
