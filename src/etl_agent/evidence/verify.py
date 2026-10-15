"""M-N 审计证据 — 整流验证报告（valid / event_count / head_hash / errors[]）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-N（整流验证报告）/ 第 5.2 章 harness_evidence_events
覆盖 FR：FR-36（整流验证报告 valid/event_count/head_hash/errors）
对应 AC：AC-09（证据可验证）、AC-02（报告不含 payload 明文）

**验证顺序自底向上**（S9 提示词 §8.1）：
1. sequence 单调递增且从 1 连续（跳号说明事件被删）；
2. `previous_hash` 与前一条的 `event_hash` 一致（断链定位）；
3. `event_hash` 与公式重算一致（篡改定位）；
4. 有签名的检查点用公钥验签（信任链锚点）。

任一步失败即 `valid=False`，`errors[]` 逐条列出**位置与原因**，绝不只给一个布尔值——
审计人员需要知道「哪里断了」，而不是「整体不通过」（SPEC 3.2 M-N）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.evidence.chain import GENESIS_HASH, compute_event_hash
from etl_agent.evidence.checkpoint import checkpoint_payload, verify_checkpoint
from etl_agent.evidence.stream import require_stream
from etl_agent.infrastructure.models import HarnessEvidenceEvent


@dataclass
class VerificationReport:
    """整流验证报告（与 SPEC 3.2 M-N 的 JSON 形态一一对应）。"""

    valid: bool
    event_count: int
    head_hash: str | None
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """回写为 SPEC 规定的报告形态（不含任何 payload 正文，AC-02）。"""
        return {
            "valid": self.valid,
            "event_count": self.event_count,
            "head_hash": self.head_hash,
            "errors": list(self.errors),
        }


async def verify_stream(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    public_keys: list[str] | None = None,
) -> VerificationReport:
    """整流验证项目的证据流，产出完整报告。

    Args:
        session: 业务库会话。
        project_id: 项目 ID。
        public_keys: 验签公钥列表（hex）；空的检查点跳过验签（不计为错误——
            无公钥是部署配置问题，不是链被篡改的证据）。

    Returns:
        VerificationReport：`valid=False` 时 `errors[]` 列出全部断点（不只第一个）。
    """
    stream = await require_stream(session, project_id)
    result = await session.execute(
        select(HarnessEvidenceEvent)
        .where(HarnessEvidenceEvent.stream_id == stream.id)
        .order_by(HarnessEvidenceEvent.sequence.asc())
    )
    events = list(result.scalars().all())

    errors: list[dict[str, Any]] = []
    expected_sequence = 1
    previous_hash: str | None = GENESIS_HASH

    for index, event in enumerate(events):
        # 1. 序号连续且单调（跳号 = 有事件被删除）
        if event.sequence != expected_sequence:
            errors.append(
                {
                    "index": index,
                    "sequence": event.sequence,
                    "reason": "sequence_not_contiguous",
                    "expected": expected_sequence,
                }
            )
            expected_sequence = event.sequence
        # 2. previous_hash 接续
        if (event.previous_hash or GENESIS_HASH) != previous_hash:
            errors.append(
                {
                    "index": index,
                    "sequence": event.sequence,
                    "reason": "previous_hash_mismatch",
                    "expected": previous_hash,
                    "actual": event.previous_hash,
                }
            )
        # 3. event_hash 重算一致（篡改检测）
        recomputed = compute_event_hash(event.previous_hash, event.payload_sha256, event.sequence)
        if recomputed != event.event_hash:
            errors.append(
                {
                    "index": index,
                    "sequence": event.sequence,
                    "reason": "event_hash_mismatch",
                    "expected": recomputed,
                    "actual": event.event_hash,
                }
            )
        # 4. 检查点验签（有签名且有公钥时）
        if event.signature_value and public_keys:
            payload = checkpoint_payload(str(stream.id), event.sequence, event.event_hash)
            if not any(
                verify_checkpoint(payload, event.signature_value, key) for key in public_keys
            ):
                errors.append(
                    {
                        "index": index,
                        "sequence": event.sequence,
                        "reason": "checkpoint_signature_invalid",
                    }
                )

        previous_hash = event.event_hash
        expected_sequence += 1

    # 链头一致性：流记录的 head_hash 必须等于末事件哈希（派生值对账）
    computed_head = events[-1].event_hash if events else None
    if stream.head_hash != computed_head:
        errors.append(
            {
                "index": len(events),
                "sequence": None,
                "reason": "head_hash_mismatch",
                "expected": computed_head,
                "actual": stream.head_hash,
            }
        )

    return VerificationReport(
        valid=not errors,
        event_count=len(events),
        head_hash=computed_head,
        errors=errors,
    )


__all__ = ["VerificationReport", "verify_stream"]
