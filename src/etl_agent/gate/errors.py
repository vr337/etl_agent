"""M-G 确定性门禁 — 门禁域错误体系。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 3.2 章 M-G
覆盖 FR：FR-19（门禁未通过时的对外错误）、FR-21（预算耗尽确定性失败）
对应 AC：AC-04（不合规候选被拦截）

`BIZ_422_GATE_FAILED` 为 SPEC 6.4 已定义错误码，S5 **复用不新增**（故不进 AMEND-004）。
错误响应只附脱敏失败摘要（规则 ID + 摘要），不含候选正文（FR-67 / AC-02）。
"""
from __future__ import annotations

from etl_agent.auth.errors import BizError


class GateFailed(BizError):
    """确定性门禁未通过（SPEC 6.4 BIZ_422_GATE_FAILED，附脱敏失败摘要）。"""

    code = "BIZ_422_GATE_FAILED"
    http_status = 422
