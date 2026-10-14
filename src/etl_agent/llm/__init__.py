"""M-F 生成引擎 — LLM Provider 包（Provider 抽象 / OpenAI 兼容 / Mock / 工厂）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-F / 第 2.2 章第三方依赖
覆盖 FR：FR-18（多 Provider 产出 EtlPlan + HOCON）、FR-24（逐行 LLM 契约的 Provider 来源）
对应 AC：AC-02（模型零凭据）、AC-04（候选经门禁裁决）
"""
