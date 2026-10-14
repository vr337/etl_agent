"""M-F 生成引擎 — LLM Provider 抽象接口与候选数据结构。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-F / 第 7.1 章主流程时序图 / 第 8 章「性能 · LLM 生成」
覆盖 FR：FR-18（多后端 / 多 Provider 产出结构化 plan + HOCON）、FR-67（Prompt 与候选正文不落业务库）
对应 AC：AC-02（模型零凭据：Provider 不可见连接密码与样本明文）、AC-03（生成链路可中断恢复）

边界（SPEC 1.3 设计原则 1 / PRD 9.2 注记）：
- Provider 只接收「需求文本 + 结构画像（列名 / 类型 / 角色）」，不接收任何凭据与样本明文；
- Provider 内部不得把 requirement 全文写入日志或落库（只记计数 / 耗时 / 非敏感诊断，FR-67）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class GeneratedCandidate:
    """一次生成的候选制品（S5 提示词 11.1）。

    `etl_plan` 与 `hocon` 是候选正文——只随 LangGraph Checkpoint 落在 `etl_checkpoint`
    库（ADR-03 双库分离），**禁止**写入业务库 `etl_agent`（FR-67）。
    """

    etl_plan: dict[str, Any]
    hocon: str
    backend: str
    model: str


@runtime_checkable
class LLMProvider(Protocol):
    """LLM Provider 抽象（FR-18：OpenAI 兼容多供应商可插拔）。"""

    async def generate_etl_plan(
        self,
        requirement: str,
        source_profile: dict[str, Any],
        sink_profile: dict[str, Any],
        feedback: str | None = None,
    ) -> GeneratedCandidate:
        """生成 EtlPlan + SeaTunnel HOCON 候选。

        Args:
            requirement: 自然语言需求（不得写入日志 / 业务库，FR-67）。
            source_profile: 来源端结构画像（列名 / 类型 / 角色 / 连接与表标识）。
            sink_profile: 目标端结构画像（同上）。
            feedback: 上一候选的门禁失败反馈（只含规则 ID + 脱敏摘要，S5 提示词 11.7）。

        Returns:
            GeneratedCandidate: 结构化 plan + HOCON 候选正文。

        Raises:
            LLMProviderError: Provider 调用失败 / 超时 / 返回不可解析（BIZ_422_LLM_PROVIDER_ERROR）。
        """
        ...
