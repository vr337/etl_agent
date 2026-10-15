"""M-N 审计证据 — 全量审计事件（7 类关键事件钩子 + 凭据脱敏）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N / 第 5.2 章 audit_events /
第 8 章「安全 · 审计」
覆盖 FR：FR-36（actor/project/action/resource/before/after/reason/trace_id）
对应 AC：AC-02（before/after 零明文）、AC-09（关键事件必须有证据）

模块职责：审计事件记录（`service`）、关键事件钩子（`hooks`）、错误码（`errors`）。
钩子同时双写 `audit_events` 与 `harness_evidence_*`——台账回答「谁做了什么」，
证据账本回答「记录有没有被动过」。
"""

from etl_agent.audit.hooks import (
    KEY_EVENT_TYPES,
    on_approval_decision,
    on_commit_issued,
    on_execution_transition,
    on_pdp_decision,
    on_prepare_created,
    on_rollback_event,
    on_supervision_action,
)
from etl_agent.audit.service import AuditEventInput, record, record_safely

__all__ = [
    "KEY_EVENT_TYPES",
    "AuditEventInput",
    "on_approval_decision",
    "on_commit_issued",
    "on_execution_transition",
    "on_pdp_decision",
    "on_prepare_created",
    "on_rollback_event",
    "on_supervision_action",
    "record",
    "record_safely",
]
