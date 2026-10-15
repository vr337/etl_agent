"""M-J Harness 安全执行内核 — 签名策略包（Ed25519 分离签名 + 启动验签）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J（策略内核）/ 第 8 章「安全 · 授权与职责分离」
覆盖 FR：FR-30（策略清单 rule_id/version 唯一、排序稳定 + SHA256 + Ed25519 分离签名；启动与热切换前强制验签；请求不得临时切换策略版本）
对应 AC：AC-05（Harness 协议的可信输入）、AC-01（职责分离规则的可信来源）

两道校验（任一失败即拒绝加载 → 启动拒绝）：
1. **清单自校验**：`package_sha256` 必须等于「去掉该字段后的清单」的 SHA256（防篡改）；
2. **分离签名**：Ed25519 签名覆盖整个清单文件载荷，公钥来自 `ETL_AGENT_HARNESS_PUBLIC_KEYS`
   （私钥只存在于 `ETL_AGENT_HARNESS_SIGNING_KEY_HEX`，仅签名脚本使用，控制面不持有）。

S5 吸收 7 落地：GATE-014 的 LLM 主机白名单迁入策略包 `host_whitelist`，
门禁规则（`gate.rules.row_llm_contract`）从策略包读取，不再硬编码。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

DEFAULT_PACKAGE_VERSION = "harness-v2-20260808"

# 默认 LLM 主机白名单（S5 吸收 7：由策略包承载，规则侧不再硬编码）
DEFAULT_HOST_WHITELIST: tuple[str, ...] = (
    "api.openai.com",
    "api.deepseek.com",
    "ark.cn-beijing.volces.com",
    "api.moonshot.cn",
    "open.bigmodel.cn",
)

# 9 条 PDP 规则的默认参数（可由策略包覆盖；阈值不进代码硬编码以外的分支）
DEFAULT_RULE_PARAMS: dict[str, dict[str, Any]] = {
    "HARNESS-P0-CROSS-PROJECT": {},
    "HARNESS-P0-PLAINTEXT-CREDENTIAL": {},
    "HARNESS-P0-DESTRUCTIVE-OP": {"required_approvals": 2},
    "HARNESS-P0-SECRET-EXFIL": {},
    "HARNESS-P1-SENSITIVE-EXPORT": {"sensitive_levels": ["RESTRICTED", "SECRET"]},
    "HARNESS-P1-BUDGET-EXCEED": {
        "max_records": 10_000_000,
        "max_bytes": 10 * 1024**3,
        "max_duration_seconds": 3600,
    },
    "HARNESS-P2-WRITE-PROD": {"environments": ["prod"]},
    "HARNESS-P2-SCHEMA-MISMATCH": {},
    "HARNESS-P3-ROUTINE": {},
}

RULE_IDS: tuple[str, ...] = tuple(DEFAULT_RULE_PARAMS)


class PolicyPackageInvalid(RuntimeError):
    """策略包缺失 / 清单被篡改 / 签名验签失败：启动即拒绝（FR-30）。

    失败方向选择「拒绝启动」而非「降级放行」——降级等于在没有可信策略的情况下
    接收执行意图（SPEC 8「安全 · 授权与职责分离」行）。
    """


@dataclass(frozen=True)
class PolicyRule:
    """策略清单条目（rule_id + version 唯一）。"""

    rule_id: str
    version: str
    enabled: bool
    params: dict[str, Any]


@dataclass(frozen=True)
class PolicyPackage:
    """已验签的策略包（控制面只持有公钥与验签后的清单）。"""

    version: str
    rules: tuple[PolicyRule, ...]
    host_whitelist: tuple[str, ...]
    package_sha256: str
    signature: str

    def rule(self, rule_id: str) -> PolicyRule | None:
        """按 rule_id 取规则（未登记 / 未启用返回 None）。"""
        for item in self.rules:
            if item.rule_id == rule_id and item.enabled:
                return item
        return None

    def params_of(self, rule_id: str) -> dict[str, Any]:
        """取规则参数（缺省回落到内置默认值）。"""
        item = self.rule(rule_id)
        if item is not None:
            return dict(item.params)
        return dict(DEFAULT_RULE_PARAMS.get(rule_id, {}))


def canonical_json(payload: Any) -> str:
    """规范化 JSON（键排序 + 紧凑分隔）：同清单恒得同摘要与同签名载荷。"""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def default_rules() -> tuple[PolicyRule, ...]:
    """9 条默认规则（rule_id 排序稳定）。"""
    return tuple(
        PolicyRule(rule_id=rule_id, version="1", enabled=True, params=dict(DEFAULT_RULE_PARAMS[rule_id]))
        for rule_id in RULE_IDS
    )


def build_manifest(
    rules: Sequence[PolicyRule] | None = None,
    host_whitelist: Sequence[str] | None = None,
    version: str = DEFAULT_PACKAGE_VERSION,
) -> dict[str, Any]:
    """构造清单（不含 `package_sha256`）：rule_id/version 唯一 + 排序稳定（FR-30）。"""
    items = list(rules or default_rules())
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.rule_id, item.version)
        if key in seen:
            raise PolicyPackageInvalid(f"策略清单 rule_id/version 重复：{item.rule_id}@{item.version}")
        seen.add(key)
    items.sort(key=lambda item: (item.rule_id, item.version))
    return {
        "version": version,
        "rules": [
            {
                "rule_id": item.rule_id,
                "version": item.version,
                "enabled": item.enabled,
                "params": item.params,
            }
            for item in items
        ],
        "host_whitelist": list(host_whitelist or DEFAULT_HOST_WHITELIST),
    }


def compute_package_sha256(manifest: Mapping[str, Any]) -> str:
    """清单摘要：对「不含 `package_sha256` 字段」的规范化清单取 SHA256（FR-30）。"""
    return hashlib.sha256(canonical_json(dict(manifest)).encode("utf-8")).hexdigest()


def build_package(
    rules: Sequence[PolicyRule] | None = None,
    host_whitelist: Sequence[str] | None = None,
    version: str = DEFAULT_PACKAGE_VERSION,
) -> dict[str, Any]:
    """构造完整策略包（清单 + `package_sha256`）。"""
    manifest = build_manifest(rules, host_whitelist, version)
    return {**manifest, "package_sha256": compute_package_sha256(manifest)}


def sign_package(package: Mapping[str, Any], signing_key_hex: str) -> str:
    """用 Ed25519 私钥对策略包载荷签名，返回 hex 签名（仅签名脚本使用）。"""
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(signing_key_hex.strip()))
    return private_key.sign(canonical_json(dict(package)).encode("utf-8")).hex()


def verify_package(
    package: Mapping[str, Any], signature_hex: str, public_keys_hex: Sequence[str]
) -> str:
    """验签 + 清单自校验，返回 `package_sha256`。

    Raises:
        PolicyPackageInvalid: 清单缺少 `package_sha256` / 摘要不符 / 签名无效 / 无可用公钥。
    """
    payload = dict(package)
    declared = payload.pop("package_sha256", None)
    if not isinstance(declared, str) or not declared:
        raise PolicyPackageInvalid("策略包缺少 package_sha256 字段")
    recomputed = compute_package_sha256(payload)
    if recomputed != declared:
        raise PolicyPackageInvalid("策略包清单摘要不符：清单已被篡改（FR-30）")

    keys = [item.strip() for item in public_keys_hex if item.strip()]
    if not keys:
        raise PolicyPackageInvalid("ETL_AGENT_HARNESS_PUBLIC_KEYS 未配置，无法验签（FR-30）")
    message = canonical_json({**payload, "package_sha256": declared}).encode("utf-8")
    try:
        signature = bytes.fromhex(signature_hex.strip())
    except ValueError as exc:
        raise PolicyPackageInvalid("策略包签名不是合法 hex") from exc
    for key_hex in keys:
        try:
            public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex))
            public_key.verify(signature, message)
        except (InvalidSignature, ValueError):
            continue
        return declared
    raise PolicyPackageInvalid("策略包签名验签失败：清单不可信（FR-30）")


def load_package(
    path: str | Path, signature_path: str | Path, public_keys_hex: Sequence[str]
) -> PolicyPackage:
    """从磁盘加载并验签策略包（PDP 启动与热切换前的强制入口）。

    Raises:
        PolicyPackageInvalid: 文件缺失 / JSON 非法 / 验签失败。
    """
    package_path = Path(path)
    signature_file = Path(signature_path)
    if not package_path.is_file():
        raise PolicyPackageInvalid(f"策略包不存在：{package_path}（先运行 scripts/gen_policy_package.py）")
    if not signature_file.is_file():
        raise PolicyPackageInvalid(f"策略包签名文件不存在：{signature_file}")
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PolicyPackageInvalid("策略包不是合法 JSON") from exc
    if not isinstance(package, dict):
        raise PolicyPackageInvalid("策略包根节点必须是对象")

    digest = verify_package(package, signature_file.read_text(encoding="utf-8"), public_keys_hex)
    rules = tuple(
        PolicyRule(
            rule_id=str(item["rule_id"]),
            version=str(item.get("version", "1")),
            enabled=bool(item.get("enabled", True)),
            params=dict(item.get("params") or {}),
        )
        for item in package.get("rules", [])
        if isinstance(item, dict) and item.get("rule_id")
    )
    return PolicyPackage(
        version=str(package.get("version", DEFAULT_PACKAGE_VERSION)),
        rules=rules,
        host_whitelist=tuple(str(item) for item in package.get("host_whitelist", [])),
        package_sha256=digest,
        signature=signature_file.read_text(encoding="utf-8").strip(),
    )


def package_from_mapping(payload: Mapping[str, Any]) -> PolicyPackage:
    """从已验签的清单字典构造 `PolicyPackage`（供脚本 / 测试复用，不再重复加载磁盘）。"""
    rules = tuple(
        PolicyRule(
            rule_id=str(item["rule_id"]),
            version=str(item.get("version", "1")),
            enabled=bool(item.get("enabled", True)),
            params=dict(item.get("params") or {}),
        )
        for item in payload.get("rules", [])
        if isinstance(item, Mapping) and item.get("rule_id")
    )
    return PolicyPackage(
        version=str(payload.get("version", DEFAULT_PACKAGE_VERSION)),
        rules=rules,
        host_whitelist=tuple(str(item) for item in payload.get("host_whitelist", [])),
        package_sha256=str(payload.get("package_sha256", "")),
        signature=str(payload.get("signature", "")),
    )


def package_id(package: PolicyPackage) -> str:
    """策略包标识（`version#sha256前12位`）：落 `harness_policy_decisions.policy_package_id`。

    标识由「版本 + 清单摘要」构成，同包恒得同 ID——决策可回溯到确切的策略清单（FR-30）。
    """
    return f"{package.version}#{package.package_sha256[:12]}"
