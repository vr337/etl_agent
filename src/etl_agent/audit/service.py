"""M-N 审计证据 — 全量审计事件记录服务。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N / 第 5.2 章 audit_events / 第 8 章「安全 · 审计」
覆盖 FR：FR-36（审计事件 actor/project/action/resource/before/after/reason/trace_id）
对应 AC：AC-02（before/after 不含凭证明文）、AC-09（审计覆盖）

**两条红线**：
1. **before/after 不含凭证明文**（AC-02 / PRD 10.5）：`_sanitize` 递归剥离敏感键，
   命中即替换为 `"[REDACTED]"`，宁可记不清也不记密钥；
2. **记录失败不能丢审计**：`record()` 自身不吞异常，但 `record_safely()` 捕获异常
   并降级为结构化告警日志——审计服务不可用不应让主流程整体失败（PDP 已裁决的动作
   必须能落库，否则「决策发生了、审计没有」比「审计失败」更糟）。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import AuditEvent

logger = logging.getLogger("etl_agent.audit")

# 敏感键名（小写子串匹配）：命中即脱敏（AC-02）
_SENSITIVE_KEY_MARKERS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "private_key",
    "api_key",
    "signature",
    "authorization",
    "fernet",
    "dsn",
    "connection_string",
)

# 标识类键名后缀：`token_id` / `credential_id` 是**指向凭据的引用**，不是凭据本体。
# 它们必须留痕（否则「签发了哪个令牌」在事后无从追溯），故不进脱敏集。
_IDENTIFIER_SUFFIXES: tuple[str, ...] = ("_id", "_ids", "_sha256", "_digest", "_hash")

REDACTED = "[REDACTED]"


def _is_sensitive_key(key: object) -> bool:
    """判断键名是否指向凭据本体（而非对凭据的引用）。

    `token` / `secret` 命中脱敏；`token_id` / `token_sha256` 是引用（UUID 或摘要），
    保留它们才能回答「哪一份凭据」——对摘要再脱敏等于把可追溯性也一起抹掉。
    """
    lowered = str(key).lower()
    if any(lowered.endswith(suffix) for suffix in _IDENTIFIER_SUFFIXES):
        return False
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def _sanitize(value: Any, *, depth: int = 0) -> Any:
    """递归剥离 before/after 中的凭证明文（AC-02）。

    深度上限 8：审计对象是治理事实，不是任意嵌套的业务数据；超深结构直接截断为
    字符串标记，避免恶意构造的深嵌套把服务拖进栈溢出。
    """
    if depth > 8:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
                cleaned[str(key)] = REDACTED
            else:
                cleaned[str(key)] = _sanitize(item, depth=depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, depth=depth + 1) for item in value]
    return value


@dataclass(frozen=True)
class AuditEventInput:
    """一条审计事件的治理事实（SPEC 5.2 `audit_events` 全字段）。"""

    project_id: uuid.UUID
    actor_id: uuid.UUID
    action: str
    resource_type: str
    resource_id: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    reason: str | None = None
    trace_id: str | None = None


async def record(session: AsyncSession, event: AuditEventInput) -> AuditEvent:
    """写入一条审计事件（before/after 先脱敏，AC-02）。

    调用方负责事务边界；本函数只 flush，使审计与业务变更同事务可见——
    审计与业务分属两个独立提交时，会出现「业务成功、审计丢失」的窗口，
    那正是审计最该存在的场景（S9 提示词 §8.2）。
    """
    row = AuditEvent(
        project_id=event.project_id,
        actor_id=event.actor_id,
        action=event.action,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        before=_sanitize(event.before) if event.before is not None else None,
        after=_sanitize(event.after) if event.after is not None else None,
        reason=event.reason,
        trace_id=event.trace_id,
    )
    session.add(row)
    await session.flush()
    return row


async def record_safely(session: AsyncSession, event: AuditEventInput) -> AuditEvent | None:
    """写入审计事件；失败降级为告警日志而不抛（「记录失败也要落审计」的兜底）。

    用于钩子路径：钩子在业务主流程内被调用，审计写失败若直接抛出会把
    「审计问题」升级为「业务失败」——但审计失败本身必须可见，故落 ERROR 级日志。
    """
    try:
        return await record(session, event)
    except Exception:
        logger.exception(
            "审计事件写入失败：action=%s resource=%s/%s",
            event.action,
            event.resource_type,
            event.resource_id,
        )
        return None


def now_utc() -> datetime:
    """当前时间（UTC）；便于测试注入固定时钟。"""
    return datetime.now(UTC)


__all__ = [
    "REDACTED",
    "AuditEventInput",
    "now_utc",
    "record",
    "record_safely",
]