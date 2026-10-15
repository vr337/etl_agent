"""M-J Harness 安全执行内核 — Capability 验签（Ed25519）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 8 章「安全 · 防重放与授权」
覆盖 FR：FR-33（claims 绑定校验）、FR-34（Replay Guard 输入可信令牌）
对应 AC：AC-05（单次令牌 + 防重放）

校验三件事：签名有效、令牌未过期、claims 绑定匹配（工具/版本/主体/环境/制品指纹）。
任一失败即拒绝消费，且不执行任何副作用（失败关闭）。
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from etl_agent.harness.errors import CapabilityExpired, CapabilityInvalid

# claims 中必须与「这一次调用」严格一致的绑定字段（SPEC 8「安全 · 防重放与授权」）
BOUND_CLAIM_FIELDS: tuple[str, ...] = (
    "tool_name",
    "tool_version",
    "subject",
    "environment",
    "artifact_sha256",
    "supervision_contract_sha256",
    "project_id",
    "preparation_id",
    "pipeline_version_id",
)


class CapabilityVerifier:
    """Ed25519 令牌验证器（公钥来自 `ETL_AGENT_HARNESS_PUBLIC_KEYS`）。"""

    def __init__(self, public_keys_hex: list[str]) -> None:
        keys: list[Ed25519PublicKey] = []
        for item in public_keys_hex:
            key_hex = item.strip()
            if not key_hex:
                continue
            try:
                keys.append(Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex)))
            except (ValueError, TypeError):
                continue
        if not keys:
            raise CapabilityInvalid("未配置可用的 Harness 验签公钥（FR-33）")
        self._keys = keys

    def verify(self, token: str, *, now: datetime) -> dict[str, Any]:
        """验签并返回 claims（token 为 `json.signature` 形式）。

        Raises:
            CapabilityInvalid: 令牌格式非法 / 签名无效（或 claims 缺失）。
            CapabilityExpired: 令牌已过期（TTL ≤900s）。
        """
        payload = self._decode(token)
        if int(payload.get("exp", 0)) <= int(now.timestamp()):
            raise CapabilityExpired("Capability 已过期（TTL ≤900s，FR-33）")
        claims = payload.get("claims")
        if not isinstance(claims, dict):
            raise CapabilityInvalid("Capability claims 缺失")
        return claims

    def verify_against(self, token: str, *, now: datetime, expected: Mapping[str, Any]) -> dict[str, Any]:
        """验签 + 绑定校验：claims 的绑定字段必须与期望事实一致。

        Raises:
            CapabilityInvalid: 任一绑定字段漂移（换制品 / 换工具 / 换环境 / 换主体均拒绝）。
        """
        claims = self.verify(token, now=now)
        # 只对调用方显式声明的绑定字段做比对（`expected` 中的 None 表示不校验该项）
        mismatched = [
            field
            for field in BOUND_CLAIM_FIELDS
            if expected.get(field) is not None
            and str(claims.get(field)) != str(expected.get(field))
        ]
        if mismatched:
            raise CapabilityInvalid(f"Capability claims 绑定不匹配：{', '.join(mismatched)}")
        return claims

    def _decode(self, token: str) -> dict[str, Any]:
        """解码并验签：签名必须能被任一公钥验证（FR-33）。"""
        if not token or "." not in token:
            raise CapabilityInvalid("Capability 格式非法（应为 <json>.<signature>）")
        body, _, signature_hex = token.rpartition(".")
        try:
            envelope = json.loads(body)
            signature = bytes.fromhex(signature_hex)
            payload = envelope["payload"]
            message = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise CapabilityInvalid("Capability 载荷不可解析") from exc
        for key in self._keys:
            try:
                key.verify(signature, message)
            except InvalidSignature:
                continue
            decoded: dict[str, Any] = payload
            return decoded
        raise CapabilityInvalid("Capability 签名验签失败（FR-33）")
