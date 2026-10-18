"""M-F 生成引擎 — LLM 域错误体系。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 3.2 章 M-F
覆盖 FR：FR-18（生成调用失败可诊断）、FR-21（候选预算耗尽即确定性失败）
对应 AC：AC-04（有限修复预算 ≤3，耗尽确定性失败）

本文件两个错误码为 S5 新增（SPEC 6.4 未覆盖生成域），已生成 AMEND-004 草稿待人类审核。
错误摘要只含失败分类与计数，不含 Prompt / 候选正文（FR-67）。
"""
from __future__ import annotations

from etl_agent.auth.errors import BizError


class LLMProviderError(BizError):
    """LLM Provider 调用失败（HTTP 错误 / 响应不可解析）。"""

    code = "BIZ_422_LLM_PROVIDER_ERROR"
    http_status = 422


class LLMProviderUnavailable(LLMProviderError):
    """LLM 基础设施不可用（T-LLM-01）：超时 / 连接失败 / 额度耗尽（429）。

    与 `LLMProviderError` 分开命名并升为 **503**：这三类都是「Provider 暂时用不了」，
    不是「这次生成的内容有问题」。混进 422 会让调用方误以为需要改输入，
    也会让 SRE 把配额/网络问题当成业务错误排。

    红线：**绝不静默回落到 mock_provider** —— 回落会让一次「没生成」伪装成
    「生成了一份固定配置」，是本项目最不能接受的那类欺骗。
    """

    code = "SYS_503_LLM_UNAVAILABLE"
    http_status = 503


class LLMBudgetExhausted(BizError):
    """候选预算耗尽（FR-21：最多 3 份候选，含初始候选；耗尽即确定性失败）。"""

    code = "BIZ_422_LLM_BUDGET_EXHAUSTED"
    http_status = 422


class MockProviderForbidden(BizError):
    """mock provider 被用于生产环境（S5 提示词 11.2：mock 仅测试用）。

    与 `LLMProviderError` 分开命名：这是配置错误而非调用失败，便于运维定位。
    """

    code = "SYS_500_INTERNAL"
    http_status = 500
