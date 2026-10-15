"""M-N 审计证据 — 证据账本域错误体系。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 3.2 章 M-N / 第 5.2 章 harness_evidence_streams
覆盖 FR：FR-36（不可篡改证据账本 + 整流验证报告）
对应 AC：AC-09（Benchmark 的证据覆盖率门禁依赖本域可用）

口径（S9 提示词 §8.10）：新增 3 个证据域码，其余复用 SPEC 6.4 与既有 AMEND：

| 场景 | 码 | 来源 |
|------|----|------|
| 证据流不存在 / 跨项目 | `BIZ_404_EVIDENCE_STREAM_NOT_FOUND` | S9 新增（→ AMEND-008） |
| 证据链断链（previous_hash 与链头不符） | `BIZ_409_EVIDENCE_CHAIN_BROKEN` | S9 新增（→ AMEND-008） |
| 整流验证失败（哈希不匹配 / 顺序不单调） | `BIZ_422_EVIDENCE_VERIFY_FAILED` | S9 新增（→ AMEND-008） |

错误摘要一律不含凭据与事件 payload 正文（AC-02）。
"""
from __future__ import annotations

from etl_agent.auth.errors import BizError


class EvidenceStreamNotFound(BizError):
    """证据流不存在或跨项目（不泄露存在性）。"""

    code = "BIZ_404_EVIDENCE_STREAM_NOT_FOUND"
    http_status = 404


class EvidenceChainBroken(BizError):
    """证据链断链：追加事件的 `previous_hash` 与流头 `head_hash` 不一致。"""

    code = "BIZ_409_EVIDENCE_CHAIN_BROKEN"
    http_status = 409


class EvidenceVerifyFailed(BizError):
    """整流验证失败（哈希不匹配 / 顺序不单调 / 检查点签名无效）。"""

    code = "BIZ_422_EVIDENCE_VERIFY_FAILED"
    http_status = 422


__all__ = ["EvidenceChainBroken", "EvidenceStreamNotFound", "EvidenceVerifyFailed"]
