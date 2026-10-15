"""M-N 审计证据 — 证据账本 API 出入参模型（查询证据流 / 事件列表 / 整流验证）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N / 第 5.2 章 harness_evidence_streams
覆盖 FR：FR-36（证据流查询与整流验证报告）
对应 AC：AC-09（证据覆盖率）、AC-02（响应零明文）

红线：出参只含**治理事实与摘要**——`payload_sha256` 是摘要，事件正文（`payload`）
不入库也不回传（AC-02 / S9 禁止事项 4）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class EvidenceStreamView(BaseModel):
    """证据流视图（head_hash 为链头派生态）。"""

    id: uuid.UUID
    project_id: uuid.UUID
    head_hash: str | None = None
    created_at: datetime


class EvidenceEventView(BaseModel):
    """单条证据事件视图（只含摘要与治理字段，不含正文）。"""

    id: uuid.UUID
    stream_id: uuid.UUID
    sequence: int
    payload_sha256: str
    previous_hash: str | None = None
    event_hash: str
    signature_algorithm: str | None = None
    signature_key_id: str | None = None
    occurred_at: datetime
    actor_id: uuid.UUID | None = None
    event_type: str
    risk_level: str | None = None
    policy_decision_id: uuid.UUID | None = None
    trace_id: str | None = None


class EvidenceEventListResponse(BaseModel):
    """事件列表（分页由 limit/after_sequence 表达，避免深分页偏移）。"""

    stream_id: uuid.UUID
    event_count: int
    events: list[EvidenceEventView]


class VerifyResponse(BaseModel):
    """整流验证报告（valid / event_count / head_hash / errors[]）。"""

    valid: bool
    event_count: int
    head_hash: str | None = None
    errors: list[dict[str, Any]]


__all__ = [
    "EvidenceEventListResponse",
    "EvidenceEventView",
    "EvidenceStreamView",
    "VerifyResponse",
]
