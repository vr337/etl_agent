"""M-N 审计证据 — 哈希链计算与校验。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N / 第 5.2 章 harness_evidence_events
覆盖 FR：FR-36（event_hash / previous_hash / payload_sha256 哈希链）
对应 AC：AC-09（Benchmark 证据覆盖率门禁）、AC-02（payload 摘要不泄漏明文）

**链式公式**（S9 提示词 §8.1，SPEC 3.2 M-N 红线）：

    event_hash = SHA256(previous_hash ‖ payload_sha256 ‖ sequence)

三条不变量：
1. 首事件的 `previous_hash` 为 `GENESIS_HASH`（64 个 0），不是 None——
   空值会让「首事件被替换」无法与「链头为空」区分；
2. 拼接用 `‖` 分隔符与固定 64-bit 大端 sequence 编码，避免 `("ab","c")` 与
   `("a","bc")` 碰撞（域分隔编码，非裸字符串拼接）；
3. `payload_sha256` 由 `payload_digest()` 对规范化 JSON 计算——同 payload 恒得同摘要，
   键序变化不影响（否则重放检测会被键序噪声击穿）。
"""
from __future__ import annotations

import hashlib
import json
import struct
from typing import Any

# 创世哈希：首事件的 previous_hash（全 0，长度与 event_hash 一致）
GENESIS_HASH = "0" * 64

# 链式编码版本：变更公式时必须同时改此版本，否则历史链无法区分新旧算法
CHAIN_VERSION = "chain-v1"


def canonical_json(payload: Any) -> str:
    """规范化 JSON（键排序 + 紧凑分隔）：同 payload 恒得同摘要。"""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def payload_digest(payload: Any) -> str:
    """payload 摘要（SHA256 hex）：事件正文只以摘要入链，原文明文不入库（AC-02）。"""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def compute_event_hash(previous_hash: str | None, payload_sha256: str, sequence: int) -> str:
    """计算 `event_hash = SHA256(previous_hash ‖ payload_sha256 ‖ sequence)`。

    Args:
        previous_hash: 前序事件哈希；首事件传 None 或 GENESIS_HASH。
        payload_sha256: 事件 payload 摘要（64-hex）。
        sequence: 流内单调递增序号（从 1 开始）。

    Returns:
        64-hex 事件哈希。

    编码细节：三个字段以固定宽度 / 长度前缀编码后拼接，杜绝字段边界歧义。
    """
    prev = previous_hash if previous_hash else GENESIS_HASH
    # 长度前缀编码：prev(64) + payload(64) 定长，sequence 固定 8 字节大端
    material = (
        prev.encode("ascii")
        + payload_sha256.encode("ascii")
        + struct.pack(">Q", sequence)
    )
    return hashlib.sha256(material).hexdigest()


def verify_link(
    *, previous_hash: str | None, payload_sha256: str, sequence: int, event_hash: str
) -> bool:
    """校验单条事件的哈希是否与公式一致（用于整流验证与断链定位）。"""
    return compute_event_hash(previous_hash, payload_sha256, sequence) == event_hash


__all__ = [
    "CHAIN_VERSION",
    "GENESIS_HASH",
    "canonical_json",
    "compute_event_hash",
    "payload_digest",
    "verify_link",
]
