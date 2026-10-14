"""M-F 生成引擎 — OpenAI 兼容 Provider（httpx 异步，多供应商共用）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-F / 第 2.2 章第三方依赖（OpenAI 兼容多供应商）/ 第 8 章「性能 · LLM 生成」
覆盖 FR：FR-18（EtlPlan / HOCON 候选生成）、FR-67（Prompt 与候选正文不落业务库）
对应 AC：AC-02（模型零凭据）、AC-04（候选交门禁裁决）

三条红线（S5 提示词 11.1 / 第十八节关键决策点）：
1. 统一超时 `ETL_AGENT_LLM_TIMEOUT_SECONDS`（默认 180s，SPEC 8「性能 · LLM 生成」）；
2. **不记 Prompt**：本模块不写任何日志、不落任何库，requirement 只出现在请求体中；
3. 失败分类：超时 / HTTP 状态 / 响应不可解析三类，供遥测记录 `failure_category`。
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from etl_agent.config import Settings
from etl_agent.llm.errors import LLMProviderError
from etl_agent.llm.provider import GeneratedCandidate

# 失败分类（遥测只记分类名，不记正文，FR-67）
FAILURE_TIMEOUT = "LLM_TIMEOUT"
FAILURE_CONNECT = "LLM_CONNECT_ERROR"
FAILURE_HTTP = "LLM_HTTP_ERROR"
FAILURE_PAYLOAD = "LLM_BAD_PAYLOAD"

SYSTEM_PROMPT = (
    "你是 SeaTunnel 配置生成器。根据给定的来源/目标结构画像与自然语言需求，"
    "输出严格 JSON：{\"etl_plan\": {...}, \"hocon\": \"...\"}。"
    "凭据一律使用 ${ENV_VAR} 占位符，禁止输出任何明文口令。"
)


class OpenAICompatibleProvider:
    """OpenAI 兼容端点 Provider（openai / deepseek / doubao / kimi / zhipu / custom 共用）。"""

    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        # 传输层可注入：单测用 MockTransport 复现超时 / HTTP 错误 / 坏载荷三类失败
        self._transport = transport

    @property
    def model(self) -> str:
        return self._settings.llm_model

    async def generate_etl_plan(
        self,
        requirement: str,
        source_profile: dict[str, Any],
        sink_profile: dict[str, Any],
        feedback: str | None = None,
    ) -> GeneratedCandidate:
        """调用 `/chat/completions` 生成候选（超时 / 失败均收敛为 LLMProviderError）。

        Raises:
            LLMProviderError: 超时、连接失败、非 2xx、或响应体不可解析为
                `{etl_plan, hocon}`（message 只含分类与状态码，不含 Prompt 正文）。
        """
        payload: dict[str, Any] = {
            "model": self._settings.llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "requirement": requirement,
                            "source_profile": source_profile,
                            "sink_profile": sink_profile,
                            "gate_feedback": feedback,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0,
        }
        url = f"{self._settings.llm_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.llm_timeout_seconds, transport=self._transport
            ) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise LLMProviderError(f"{FAILURE_TIMEOUT}: 生成请求超时") from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"{FAILURE_CONNECT}: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise LLMProviderError(f"{FAILURE_HTTP}: status={response.status_code}")
        return self._parse(response.text)

    def _parse(self, body: str) -> GeneratedCandidate:
        """解析响应：content 必须是含 `etl_plan` + `hocon` 的 JSON 对象。"""
        try:
            envelope = json.loads(body)
            content = envelope["choices"][0]["message"]["content"]
            data = _load_json_object(content)
            plan = data["etl_plan"]
            hocon = data["hocon"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMProviderError(f"{FAILURE_PAYLOAD}: 响应不可解析为 etl_plan + hocon") from exc
        if not isinstance(plan, dict) or not isinstance(hocon, str) or not hocon.strip():
            raise LLMProviderError(f"{FAILURE_PAYLOAD}: etl_plan 必须是对象且 hocon 非空")
        return GeneratedCandidate(
            etl_plan=plan,
            hocon=hocon,
            backend="openai-compatible",
            model=self._settings.llm_model,
        )


def _load_json_object(content: str) -> dict[str, Any]:
    """从模型输出中提取 JSON 对象（容忍 ```json 围栏与前后解释性文本）。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text.removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no json object in content")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise TypeError("json root is not an object")
    return parsed
