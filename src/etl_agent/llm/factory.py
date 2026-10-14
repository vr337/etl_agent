"""M-F 生成引擎 — Provider 工厂（按 `ETL_AGENT_LLM_PROVIDER` 选择实现）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-F / 第 2.2 章第三方依赖 / 第 8 章「可扩展 · 新 LLM Provider 可插拔」
覆盖 FR：FR-18（OpenAI 兼容多供应商可插拔）、FR-24（逐行 LLM 契约的 Provider 来源）
对应 AC：AC-04（候选经门禁裁决）、AC-02（凭据不下发到模型）

设计：openai / deepseek / doubao / kimi / zhipu / custom 六者协议一致（OpenAI 兼容
`/chat/completions`），共用 `OpenAICompatibleProvider`，差异只在 BASE_URL / MODEL；
未显式配置 BASE_URL 时按 provider 取内置默认端点，避免每加一个供应商就写一份代码。
"""
from __future__ import annotations

from etl_agent.config import Settings
from etl_agent.llm.mock_provider import MockProvider
from etl_agent.llm.openai_provider import OpenAICompatibleProvider
from etl_agent.llm.provider import LLMProvider

# 内置默认端点（SPEC 2.2 列举的 OpenAI 兼容供应商；均可被 ETL_AGENT_LLM_BASE_URL 覆盖）
DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "doubao": "https://ark.cn-beijing.volces.com/api/v3",
    "kimi": "https://api.moonshot.cn/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
}


def create_provider(settings: Settings) -> LLMProvider:
    """按配置构造 Provider（mock 或 OpenAI 兼容）。

    Args:
        settings: 应用配置（provider / key / base_url / model 均取自 `ETL_AGENT_LLM_*`）。

    Returns:
        LLMProvider: mock 时返回 MockProvider，其余返回 OpenAI 兼容实现。
    """
    if settings.llm_provider == "mock":
        return MockProvider(settings=settings)
    base_url = settings.llm_base_url.strip() or DEFAULT_BASE_URLS.get(settings.llm_provider, "")
    if base_url != settings.llm_base_url:
        # 不修改全局 settings（避免把默认端点写回配置真相），只在本实例上覆盖
        settings = settings.model_copy(update={"llm_base_url": base_url})
    return OpenAICompatibleProvider(settings)
