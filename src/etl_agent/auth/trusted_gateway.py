"""M-A 认证鉴权 — trusted-gateway HMAC-SHA256 验签。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-A / 第 8 章「安全 · 认证」行
覆盖 FR：FR-65（HMAC-SHA256 签名：actor/timestamp/nonce/METHOD/path+query；时钟偏移 ≤60s）
对应 AC：AC-11（伪造签名 / 过期时间戳 / 缺 Header / 重放 → 401）
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time

from etl_agent.auth.errors import BadSignature, TimestampSkew

# Header 约定（S2 定义，前端/网关按此签名）
HEADER_ACTOR = "X-Gateway-Actor"
HEADER_TIMESTAMP = "X-Gateway-Timestamp"
HEADER_NONCE = "X-Gateway-Nonce"
HEADER_SIGNATURE = "X-Gateway-Signature"

# 时钟偏移容忍：±60s（FR-65 / SPEC 8）。取宽松值需同时放大 Nonce TTL（nonce_store.NONCE_TTL_SECONDS）
CLOCK_SKEW_TOLERANCE_SECONDS = 60

# Nonce 长度与字符集（FR-65：16–128 位安全字符）
NONCE_MIN_LENGTH = 16
NONCE_MAX_LENGTH = 128
NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")


def build_canonical_payload(actor: str, timestamp: str, nonce: str, method: str, target: str) -> str:
    """规范载荷：{actor}\\n{timestamp}\\n{nonce}\\n{METHOD}\\n{path}?{query}（FR-65）。

    签名与验签必须共用本函数，杜绝两侧拼装不一致；target 无 query 时不含 "?"。
    """
    return f"{actor}\n{timestamp}\n{nonce}\n{method.upper()}\n{target}"


def compute_signature(secret: str, payload: str) -> str:
    """HMAC-SHA256 十六进制签名（FR-65）。"""
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def build_request_target(path: str, query: str) -> str:
    """path?query：无 query 时退回纯 path（与 build_canonical_payload 的约定一致）。"""
    return f"{path}?{query}" if query else path


def validate_nonce_format(nonce: str) -> None:
    """Nonce 长度 / 字符集校验（FR-65：16–128 位 [A-Za-z0-9_\\-]）。

    Raises:
        BadSignature: 长度越界或字符集不合法（并入 401，不区分具体原因）。
    """
    if not (NONCE_MIN_LENGTH <= len(nonce) <= NONCE_MAX_LENGTH) or not NONCE_PATTERN.match(nonce):
        raise BadSignature("Nonce 长度或字符集不合法")


def verify_timestamp(timestamp_raw: str, now: int | None = None) -> int:
    """时钟偏移校验（FR-65：≤60s）。

    Args:
        timestamp_raw: X-Gateway-Timestamp 原值（unix-epoch-seconds 字符串）。
        now: 当前 unix 秒（测试可注入；缺省取系统时间）。

    Returns:
        int: 解析后的时间戳（验签通过后由调用方丢弃，仅用于偏移判定）。

    Raises:
        TimestampSkew:  偏移 > 60s。
        BadSignature:   时间戳不可解析（视为畸形 Header）。
    """
    try:
        timestamp = int(timestamp_raw)
    except ValueError as exc:
        raise BadSignature("时间戳格式非法") from exc
    current = int(time.time()) if now is None else now
    if abs(current - timestamp) > CLOCK_SKEW_TOLERANCE_SECONDS:
        raise TimestampSkew(f"时钟偏移超限（>{CLOCK_SKEW_TOLERANCE_SECONDS}s）")
    return timestamp


def verify_signature(secret: str, provided: str, payload: str) -> None:
    """HMAC 常量时间比对（FR-65）。

    Raises:
        BadSignature: 签名不匹配。
    """
    expected = compute_signature(secret, payload)
    # compare_digest 防时序侧信道（SPEC 8「安全 · 认证」行）
    if not hmac.compare_digest(expected, provided):
        raise BadSignature("签名校验失败")
