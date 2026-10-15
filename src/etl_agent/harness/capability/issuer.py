"""M-J Harness 安全执行内核 — Capability 签发（Ed25519 单次令牌）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 5.2 章 harness_capability_grants / 第 6.2 章 API-10
覆盖 FR：FR-33（单次 Capability：TTL 30–900s 默认 300s；绑定工具/版本/主体/环境/制品指纹/监督契约哈希/分级报告哈希/允许减损动作）
对应 AC：AC-05（签发 Ed25519 单次 Capability，TTL ≤300s）

claims 绑定（SPEC 8「安全 · 防重放与授权」）：令牌只对**这一份制品 + 这一个工具 + 这个主体 + 这个环境**有效，
换任何一项即验签失败（`HARNESS_401_CAPABILITY_INVALID`）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from etl_agent.harness.policy.package import PolicyPackageInvalid


class CapabilityIssuanceError(PolicyPackageInvalid):
    """签发失败：签名密钥缺失或非法（启动期即应拒绝，失败关闭）。

    继承 `PolicyPackageInvalid`：两者都是「可信密钥不可用」的启动期失败，
    处置一致（拒绝启动 / 拒绝签发），不对外暴露为 API 错误码。
    """


DEFAULT_TTL_SECONDS = 300
MIN_TTL_SECONDS = 30
MAX_TTL_SECONDS = 900

DEFAULT_ALLOWED_MITIGATIONS: tuple[str, ...] = ("CANCEL", "PAUSE", "THROTTLE", "QUARANTINE")


@dataclass(frozen=True)
class CapabilityClaims:
    """Capability claims（与 `harness_capability_grants.claims` 一一对应）。"""

    tool_name: str
    tool_version: str
    subject: str
    environment: str
    artifact_sha256: str
    supervision_contract_sha256: str
    classification_report_sha256: str
    allowed_mitigations: list[str]
    project_id: str
    preparation_id: str
    pipeline_version_id: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "subject": self.subject,
            "environment": self.environment,
            "artifact_sha256": self.artifact_sha256,
            "supervision_contract_sha256": self.supervision_contract_sha256,
            "classification_report_sha256": self.classification_report_sha256,
            "allowed_mitigations": self.allowed_mitigations,
            "project_id": self.project_id,
            "preparation_id": self.preparation_id,
            "pipeline_version_id": self.pipeline_version_id,
        }


@dataclass(frozen=True)
class IssuedCapability:
    """签发结果（token 本体只返回一次；库中只存 token_sha256）。"""

    token_id: uuid.UUID
    token: str
    claims: dict[str, Any]
    token_sha256: str
    expires_at: datetime


def resolve_ttl(requested: int | None) -> int:
    """解析 TTL：默认 300s，夹在 30–900s（SPEC 6.2 / 禁止事项 17）。"""
    ttl = DEFAULT_TTL_SECONDS if requested is None else requested
    return max(MIN_TTL_SECONDS, min(MAX_TTL_SECONDS, ttl))


class CapabilityIssuer:
    """Ed25519 令牌签发器（私钥只来自 `ETL_AGENT_HARNESS_SIGNING_KEY_HEX`）。"""

    def __init__(self, signing_key_hex: str) -> None:
        key_hex = signing_key_hex.strip()
        if not key_hex:
            raise CapabilityIssuanceError("未配置 Harness 签名私钥（FR-33）")
        try:
            self._private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key_hex))
        except (ValueError, TypeError) as exc:
            raise CapabilityIssuanceError("Harness 签名私钥不是合法 Ed25519 私钥（32 字节 hex）") from exc

    def issue(
        self,
        claims: CapabilityClaims,
        *,
        now: datetime,
        ttl_seconds: int | None = None,
    ) -> IssuedCapability:
        """签发：构造 claims 载荷 → 签名 → 返回令牌与摘要。

        令牌正文 = `version.claims.exp.iat.signature`，服务端与 Worker 均可离线验签。
        """
        import hashlib
        import json

        ttl = resolve_ttl(ttl_seconds)
        expires_at = now + timedelta(seconds=ttl)
        payload: dict[str, Any] = {
            "version": "capability-v1",
            "claims": claims.to_payload(),
            "exp": int(expires_at.timestamp()),
            "iat": int(now.timestamp()),
        }
        message = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        signature = self._private.sign(message).hex()
        body = json.dumps({"payload": payload}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return IssuedCapability(
            token_id=uuid.uuid4(),
            token=f"{body}.{signature}",
            claims=claims.to_payload(),
            token_sha256=hashlib.sha256(f"{body}.{signature}".encode()).hexdigest(),
            expires_at=expires_at,
        )


def hash_token(token: str) -> str:
    """令牌摘要：库中只存摘要，不存令牌本体（防库泄露即令牌泄露）。"""
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issuance_failure_now() -> datetime:
    """当前时间（UTC，秒精度）；便于测试注入固定时钟。"""
    return datetime.now(UTC)
