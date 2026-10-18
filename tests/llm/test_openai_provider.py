"""OpenAI 兼容 Provider 测试（T-LLM-01）。

覆盖：正常生成与请求体结构（response_format + few-shot）；Markdown 围栏提取；
缺字段严格抛错；超时 / 429 → 503；其它 HTTP 错误 → 422；
`response_format` 不被支持时的协议降级；门禁反馈透传；无 Key 启动期拒绝。
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from etl_agent.config import ProductionConfigError
from etl_agent.llm.errors import LLMProviderError, LLMProviderUnavailable
from etl_agent.llm.openai_provider import SYSTEM_PROMPT, OpenAICompatibleProvider
from tests.helpers import make_settings

SOURCE_PROFILE: dict[str, Any] = {
    "connection_id": "11111111-1111-1111-1111-111111111111",
    "connector_type": "POSTGRESQL",
    "schema_name": "public",
    "table_name": "orders",
    "columns": [{"name": "id", "type": "bigint", "nullable": False, "is_primary_key": True}],
}
SINK_PROFILE: dict[str, Any] = {
    "connection_id": "22222222-2222-2222-2222-222222222222",
    "connector_type": "DORIS",
    "schema_name": "dw",
    "table_name": "order_wide",
    "columns": [{"name": "id", "type": "bigint"}],
}

GOOD_CONTENT = json.dumps(
    {
        "etl_plan": {"pipeline_name": "orders_to_order_wide", "backend": "direct"},
        "hocon": 'env {\n  job.mode = "BATCH"\n}\n',
    },
    ensure_ascii=False,
)


def _settings(**overrides: Any) -> Any:
    base: dict[str, Any] = {
        "llm_provider": "openai",
        "llm_api_key": "test-key",
        "llm_base_url": "https://llm.invalid/v1",
        "llm_model": "test-model",
        "llm_timeout_seconds": 1,
    }
    base.update(overrides)
    return make_settings(**base)


def _provider(handler: Any, **overrides: Any) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        _settings(**overrides), transport=httpx.MockTransport(handler)
    )


def _ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


async def test_generate_success_and_payload_shape() -> None:
    """成功路径：返回候选；请求体带 response_format，system 含 few-shot 与红线。"""
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return _ok(GOOD_CONTENT)

    candidate = await _provider(handler).generate_etl_plan(
        "把 orders 同步到 order_wide", SOURCE_PROFILE, SINK_PROFILE
    )

    assert candidate.etl_plan["pipeline_name"] == "orders_to_order_wide"
    assert candidate.hocon.strip()
    assert candidate.backend == "openai-compatible"
    assert candidate.model == "test-model"

    payload = captured[0]
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["temperature"] == 0
    system_text = payload["messages"][0]["content"]
    assert system_text == SYSTEM_PROMPT
    assert "顶级 ETL 架构师" in system_text
    assert "order_wide_shadow" in system_text  # few-shot 示例在场


def test_system_prompt_guards() -> None:
    """System Prompt 必须写死：禁解释文字、占位符、禁 not(...)、修复模式、driver 必填。"""
    assert "禁止任何解释性文字" in SYSTEM_PROMPT
    assert "${SRC_PASSWORD}" in SYSTEM_PROMPT
    assert "禁止写 not (X IS NOT NULL)" in SYSTEM_PROMPT
    assert "gate_feedback" in SYSTEM_PROMPT
    assert "max_reject_rate" in SYSTEM_PROMPT
    # run31 实证：漏 driver 会在提交时被 SeaTunnel 拒（API-02 the options('driver') are required）
    assert "每个 Jdbc 块都必须写 driver" in SYSTEM_PROMPT
    assert "com.mysql.cj.jdbc.Driver" in SYSTEM_PROMPT
    assert "org.postgresql.Driver" in SYSTEM_PROMPT
    # run32 实证：sink 用 table 形态时缺 generate_sink_sql → 报 options('query') are required
    assert "generate_sink_sql" in SYSTEM_PROMPT
    assert "enable_upsert" in SYSTEM_PROMPT
    assert SYSTEM_PROMPT.count("generate_sink_sql = true") >= 2  # 约定段 1 + 示例 2 个 sink


def test_few_shot_contains_driver() -> None:
    """few-shot 示例本身必须含 driver —— 模型会照抄示例，示例缺什么它就缺什么。

    注意：示例经 `json.dumps` 序列化进 Prompt，引号会转义成 `\\"`，
    故按类名匹配，不按 `driver = "..."` 字面量匹配。
    """
    assert SYSTEM_PROMPT.count("com.mysql.cj.jdbc.Driver") >= 3  # 约定段 1 + 示例 source/sink 3


async def test_parse_markdown_fence() -> None:
    """模型把 JSON 包在 ```json 围栏里时仍能提取（只剥围栏，不改内容）。"""
    fenced = "```json\n" + GOOD_CONTENT + "\n```"

    def handler(request: httpx.Request) -> httpx.Response:
        return _ok(fenced)

    candidate = await _provider(handler).generate_etl_plan("需求", SOURCE_PROFILE, SINK_PROFILE)
    assert candidate.etl_plan["pipeline_name"] == "orders_to_order_wide"


async def test_parse_missing_fields_raises() -> None:
    """缺 etl_plan / hocon → 抛 422，**不用正则或默认值补**。"""
    for content in (
        json.dumps({"etl_plan": {"pipeline_name": "x"}}),
        json.dumps({"hocon": "env {}"}),
        json.dumps({"etl_plan": "not-an-object", "hocon": "env {}"}),
        json.dumps({"etl_plan": {"a": 1}, "hocon": "   "}),
        "完全不是 JSON",
    ):

        def handler(request: httpx.Request, _content: str = content) -> httpx.Response:
            return _ok(_content)

        with pytest.raises(LLMProviderError) as excinfo:
            await _provider(handler).generate_etl_plan("需求", SOURCE_PROFILE, SINK_PROFILE)
        assert excinfo.value.code == "BIZ_422_LLM_PROVIDER_ERROR"
        assert "LLM_BAD_PAYLOAD" in excinfo.value.message


async def test_timeout_raises_unavailable() -> None:
    """超时 → SYS_503_LLM_UNAVAILABLE（不是 422，更不回落 mock）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(LLMProviderUnavailable) as excinfo:
        await _provider(handler).generate_etl_plan("REQ-MARKER", SOURCE_PROFILE, SINK_PROFILE)

    assert excinfo.value.code == "SYS_503_LLM_UNAVAILABLE"
    assert excinfo.value.http_status == 503
    assert "LLM_TIMEOUT" in excinfo.value.message
    assert "REQ-MARKER" not in excinfo.value.message


async def test_rate_limit_raises_unavailable() -> None:
    """额度耗尽（429）→ 503，与「输入有问题」区分开。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "quota exceeded"})

    with pytest.raises(LLMProviderUnavailable) as excinfo:
        await _provider(handler).generate_etl_plan("需求", SOURCE_PROFILE, SINK_PROFILE)
    assert excinfo.value.code == "SYS_503_LLM_UNAVAILABLE"
    assert "LLM_RATE_LIMITED" in excinfo.value.message


async def test_http_error_raises_provider_error() -> None:
    """其它非 2xx（如模型名错 400）→ 422，只带状态码。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(LLMProviderError) as excinfo:
        await _provider(handler).generate_etl_plan("需求", SOURCE_PROFILE, SINK_PROFILE)
    assert excinfo.value.code == "BIZ_422_LLM_PROVIDER_ERROR"
    assert "LLM_HTTP_ERROR" in excinfo.value.message
    assert "500" in excinfo.value.message


async def test_response_format_degraded_once() -> None:
    """400 且提示不支持 response_format → 去掉该字段重发一次（协议降级，非静默降级）。"""
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        if "response_format" in payload:
            return httpx.Response(400, json={"error": "response_format is unsupported"})
        return _ok(GOOD_CONTENT)

    candidate = await _provider(handler).generate_etl_plan("需求", SOURCE_PROFILE, SINK_PROFILE)

    assert candidate.hocon.strip()
    assert len(calls) == 2
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


async def test_degraded_retry_still_fails_loudly() -> None:
    """降级后仍失败 → 照常抛 422，绝不吞掉错误。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "response_format is unsupported"})

    with pytest.raises(LLMProviderError) as excinfo:
        await _provider(handler).generate_etl_plan("需求", SOURCE_PROFILE, SINK_PROFILE)
    assert "LLM_HTTP_ERROR" in excinfo.value.message


async def test_feedback_passed_in_user_message() -> None:
    """门禁反馈必须进 user 消息的 gate_feedback 字段（重试闭环依赖它）。"""
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return _ok(GOOD_CONTENT)

    await _provider(handler).generate_etl_plan(
        "需求",
        SOURCE_PROFILE,
        SINK_PROFILE,
        feedback="GATE-002 不支持的连接器：source=MONGODB",
    )

    user_payload = json.loads(captured[0]["messages"][1]["content"])
    assert "GATE-002" in user_payload["gate_feedback"]
    assert user_payload["requirement"] == "需求"
    assert user_payload["source_profile"]["table_name"] == "orders"


def test_missing_api_key_rejected_by_config() -> None:
    """无 Key 时启动期即拒绝（fail-fast，不发请求）。"""
    settings = make_settings(
        llm_provider="openai", llm_api_key="", llm_base_url="", llm_model=""
    )
    with pytest.raises(ProductionConfigError) as excinfo:
        settings.validate_llm()
    assert "ETL_AGENT_LLM_API_KEY" in str(excinfo.value)


def test_timeout_default_is_120() -> None:
    """T-LLM-01：默认超时 120s（原 180s 属遗留）。"""
    assert make_settings().llm_timeout_seconds == 120
