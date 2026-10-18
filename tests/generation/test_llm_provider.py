"""LLM Provider 测试（T-09；S5 提示词第十二节 test_llm_provider）。

覆盖：mock 固定响应；无 API Key 时真实 Provider 用例 skip（不 fail）；
超时 → SYS_503_LLM_UNAVAILABLE（T-LLM-01 分级）；HTTP 错误 / 坏载荷 → BIZ_422。
"""
from __future__ import annotations

import httpx
import pytest

from etl_agent.llm.errors import LLMProviderError, LLMProviderUnavailable, MockProviderForbidden
from etl_agent.llm.mock_provider import MockProvider
from etl_agent.llm.openai_provider import OpenAICompatibleProvider
from tests.helpers import make_settings

SOURCE_PROFILE = {
    "connection_id": "11111111-1111-1111-1111-111111111111",
    "connector_type": "POSTGRESQL",
    "schema_name": "public",
    "table_name": "orders",
    "endpoint_role": "SOURCE",
    "columns": [{"name": "id", "type": "bigint"}, {"name": "amount", "type": "numeric"}],
}
SINK_PROFILE = {
    "connection_id": "22222222-2222-2222-2222-222222222222",
    "connector_type": "DORIS",
    "schema_name": "dw",
    "table_name": "orders_wide",
    "endpoint_role": "SINK",
    "columns": [{"name": "id", "type": "bigint"}, {"name": "amount", "type": "numeric"}],
}


async def test_llm_provider_mock() -> None:
    """mock provider 返回固定 EtlPlan + HOCON，且凭据一律为占位符（FR-18 / FR-68）。"""
    provider = MockProvider()
    candidate = await provider.generate_etl_plan("需求", SOURCE_PROFILE, SINK_PROFILE)
    assert candidate.etl_plan["source"]["table"] == "orders"
    assert candidate.etl_plan["sink"]["table"] == "orders_wide"
    assert candidate.backend == "mock"
    # 凭据只以 ${} 占位符出现，正文不含任何明文口令（AC-02）
    assert "${SRC_PASSWORD}" in candidate.hocon
    assert "password" in candidate.hocon

    again = await provider.generate_etl_plan("需求", SOURCE_PROFILE, SINK_PROFILE)
    assert again.etl_plan == candidate.etl_plan
    assert again.hocon == candidate.hocon


async def test_llm_provider_mock_forbidden_in_production() -> None:
    """生产启用 mock → 拒绝（S5 提示词 11.2：mock 仅测试用）。"""
    provider = MockProvider(settings=make_settings(env="prod"))
    with pytest.raises(MockProviderForbidden):
        await provider.generate_etl_plan("需求", SOURCE_PROFILE, SINK_PROFILE)


async def test_llm_provider_openai_skip() -> None:
    """无真实 API Key 时跳过（不 fail）——CI 与本地无 Key 环境均可跑。"""
    settings = make_settings(llm_provider="openai", llm_api_key="", llm_base_url="", llm_model="")
    if not settings.llm_api_key:
        pytest.skip("未配置 ETL_AGENT_LLM_API_KEY，跳过真实 Provider 调用")
    provider = OpenAICompatibleProvider(settings)
    candidate = await provider.generate_etl_plan("需求", SOURCE_PROFILE, SINK_PROFILE)
    assert candidate.hocon.strip()


def _provider_with(handler: object) -> OpenAICompatibleProvider:
    """注入 MockTransport 的 Provider（复现超时 / HTTP 错误 / 坏载荷）。"""
    settings = make_settings(
        llm_provider="openai",
        llm_api_key="test-key",
        llm_base_url="https://llm.invalid/v1",
        llm_model="test-model",
        llm_timeout_seconds=1,
    )
    return OpenAICompatibleProvider(settings, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


async def test_llm_timeout() -> None:
    """超时 → SYS_503_LLM_UNAVAILABLE（T-LLM-01：基础设施不可用，非 422）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider = _provider_with(handler)
    with pytest.raises(LLMProviderUnavailable) as excinfo:
        await provider.generate_etl_plan("REQ-MARKER-超时用例", SOURCE_PROFILE, SINK_PROFILE)
    assert excinfo.value.code == "SYS_503_LLM_UNAVAILABLE"
    assert excinfo.value.http_status == 503
    assert "LLM_TIMEOUT" in excinfo.value.message
    # 摘要不含需求正文
    assert "REQ-MARKER" not in excinfo.value.message


async def test_llm_http_error() -> None:
    """非 2xx → 失败分类为 HTTP，且只带状态码（不带响应正文）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    provider = _provider_with(handler)
    with pytest.raises(LLMProviderError) as excinfo:
        await provider.generate_etl_plan("需求", SOURCE_PROFILE, SINK_PROFILE)
    assert "LLM_HTTP_ERROR" in excinfo.value.message
    assert "500" in excinfo.value.message


async def test_llm_bad_payload() -> None:
    """响应不可解析为 etl_plan + hocon → BIZ_422_LLM_PROVIDER_ERROR。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "不是 JSON"}}]})

    provider = _provider_with(handler)
    with pytest.raises(LLMProviderError) as excinfo:
        await provider.generate_etl_plan("需求", SOURCE_PROFILE, SINK_PROFILE)
    assert "LLM_BAD_PAYLOAD" in excinfo.value.message
