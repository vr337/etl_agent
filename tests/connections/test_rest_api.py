"""REST API 数据源适配器（S11 T-21 / FR-05 / FR-12 / AC-02）。

覆盖：端点解析与路径拼接；认证头三种形态（Bearer / Basic / 匿名）；
分页契约（PAGE / OFFSET / CURSOR）；翻页终止条件；响应 Schema 冻结；
失败摘要脱敏；契约参数校验。
"""
from __future__ import annotations

import base64
from typing import Any

import pytest

from etl_agent.connections import rest_api
from etl_agent.connections.probes.base import ProbeConfig, ProbeError
from etl_agent.connections.probes.rest_api_probe import probe_rest_api

TOKEN = "tok-secret-1234"
SECRET = "pw-secret-5678"


class _Resp:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _Http:
    """httpx.AsyncClient 替身：按脚本返回响应并记录请求。"""

    def __init__(self, responses: list[_Resp]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _Resp:
        self.calls.append({"url": url, "params": params or {}, "headers": headers or {}})
        return self._responses.pop(0)

    async def aclose(self) -> None:
        return None


def test_parse_endpoint_defaults_and_path() -> None:
    """无 scheme 补 https；path 拼接不带双斜杠；host_label 不含路径。"""
    endpoint = rest_api.parse_endpoint("api.example.com", None, "/orders")
    assert endpoint.base_url == "https://api.example.com"
    assert endpoint.url == "https://api.example.com/orders"
    assert endpoint.host_label == "api.example.com:443"


def test_parse_endpoint_port_and_requires_host() -> None:
    """显式端口生效；host 为空 → ValueError。"""
    endpoint = rest_api.parse_endpoint("http://127.0.0.1", 8080, "orders")
    assert endpoint.url == "http://127.0.0.1:8080/orders"
    with pytest.raises(ValueError):
        rest_api.parse_endpoint("", None, None)


def test_auth_headers_three_modes() -> None:
    """Bearer / Basic / 匿名（公开 API 不带认证头）。"""
    assert rest_api.auth_headers({"username": "", "password": TOKEN}) == {
        "Authorization": f"Bearer {TOKEN}"
    }
    basic = rest_api.auth_headers({"username": "svc", "password": SECRET})
    expected = base64.b64encode(f"svc:{SECRET}".encode()).decode("ascii")
    assert basic == {"Authorization": f"Basic {expected}"}
    assert rest_api.auth_headers({"username": "", "password": ""}) == {}


def test_build_params_page_mode() -> None:
    """PAGE：页码从 start_page 起，带 size。"""
    spec = rest_api.FetchSpec(pagination=rest_api.PAGINATION_PAGE, page_size=50, start_page=1)
    assert rest_api.build_params(spec, None, 0) == {"page": "1", "size": "50"}
    assert rest_api.build_params(spec, None, 2) == {"page": "3", "size": "50"}


def test_build_params_offset_mode() -> None:
    """OFFSET：offset = 页序 × size。"""
    spec = rest_api.FetchSpec(pagination=rest_api.PAGINATION_OFFSET, page_size=20)
    assert rest_api.build_params(spec, None, 0) == {"offset": "0", "size": "20"}
    assert rest_api.build_params(spec, None, 3) == {"offset": "60", "size": "20"}


def test_build_params_cursor_mode() -> None:
    """CURSOR：首屏不带游标，后续带 cursor。"""
    spec = rest_api.FetchSpec(pagination=rest_api.PAGINATION_CURSOR, page_size=10)
    assert rest_api.build_params(spec, None, 0) == {"size": "10"}
    assert rest_api.build_params(spec, "abc", 1) == {"cursor": "abc", "size": "10"}


def test_fetch_spec_rejects_bad_input() -> None:
    """契约校验：非法分页模式 / page_size 越界。"""
    with pytest.raises(ValueError):
        rest_api.FetchSpec(pagination="GRAPHQL")
    with pytest.raises(ValueError):
        rest_api.FetchSpec(page_size=0)
    with pytest.raises(ValueError):
        rest_api.FetchSpec(page_size=rest_api.MAX_PAGE_SIZE + 1)


async def test_fetch_page_extracts_items_and_cursor() -> None:
    """从 `data.items` 取记录、从 `data.next` 取游标；认证头已注入。"""
    http = _Http([_Resp(200, {"data": {"items": [{"id": 1}, {"id": 2}], "next": "c2"}})])
    endpoint = rest_api.parse_endpoint("https://api.example.com", None, "/orders")
    spec = rest_api.FetchSpec(
        pagination=rest_api.PAGINATION_CURSOR,
        items_field="data.items",
        cursor_field="data.next",
        page_size=2,
    )

    page = await rest_api.fetch_page(
        endpoint, {"username": "", "password": TOKEN}, spec, client=http
    )

    assert [item["id"] for item in page.items] == [1, 2]
    assert page.next_cursor == "c2"
    assert http.calls[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert http.calls[0]["url"] == "https://api.example.com/orders"


async def test_fetch_page_error_masked() -> None:
    """非 2xx → RestApiError，摘要只含 host:port 与状态码，不含 token。"""
    http = _Http([_Resp(401)])
    endpoint = rest_api.parse_endpoint("https://api.example.com", None, "/orders")
    spec = rest_api.FetchSpec()

    with pytest.raises(rest_api.RestApiError) as excinfo:
        await rest_api.fetch_page(
            endpoint, {"username": "", "password": TOKEN}, spec, client=http
        )

    message = str(excinfo.value)
    assert "api.example.com:443" in message
    assert "401" in message
    assert TOKEN not in message


async def test_fetch_page_rejects_non_array() -> None:
    """响应不是记录数组 → 明确错误（不静默返回空）。"""
    http = _Http([_Resp(200, {"status": "ok"})])
    endpoint = rest_api.parse_endpoint("https://api.example.com", None, "/health")

    with pytest.raises(rest_api.RestApiError):
        await rest_api.fetch_page(
            endpoint, {"username": "", "password": ""}, rest_api.FetchSpec(), client=http
        )


async def test_iter_pages_cursor_stops_on_repeat_token() -> None:
    """游标不前进时必须终止（防服务端回环导致死循环）。"""
    http = _Http(
        [
            _Resp(200, {"items": [{"id": 1}], "next": "same"}),
            _Resp(200, {"items": [{"id": 2}], "next": "same"}),
        ]
    )
    endpoint = rest_api.parse_endpoint("https://api.example.com", None, "/orders")
    spec = rest_api.FetchSpec(
        pagination=rest_api.PAGINATION_CURSOR, items_field="items", cursor_field="next", page_size=1
    )

    pages = await rest_api.iter_pages(
        endpoint, {"username": "", "password": TOKEN}, spec, client=http
    )

    assert len(pages) == 2
    assert len(http.calls) == 2


async def test_iter_pages_none_mode_single_request() -> None:
    """NONE 模式只发一次请求。"""
    http = _Http([_Resp(200, [{"id": 1}])])
    endpoint = rest_api.parse_endpoint("https://api.example.com", None, "/orders")

    pages = await rest_api.iter_pages(
        endpoint, {"username": "", "password": ""}, rest_api.FetchSpec(), client=http
    )

    assert len(pages) == 1
    assert len(http.calls) == 1


def test_infer_columns_types_and_nullable() -> None:
    """Schema 冻结：类型映射、缺字段判可空、嵌套标记 json。"""
    columns = rest_api.infer_columns(
        [
            {"order_id": 1, "amount": 12.5, "paid": True, "note": "x", "meta": {"a": 1}},
            {"order_id": 2, "amount": None, "paid": False, "extra": "y"},
        ]
    )
    by_name = {column["name"]: column for column in columns}

    assert by_name["order_id"] == {"name": "order_id", "data_type": "bigint", "nullable": False}
    assert by_name["amount"]["data_type"] == "double"
    assert by_name["amount"]["nullable"] is True
    assert by_name["paid"]["data_type"] == "boolean"
    assert by_name["note"]["data_type"] == "string"
    assert by_name["note"]["nullable"] is True
    assert by_name["meta"]["data_type"] == "json"
    assert by_name["extra"]["nullable"] is True


def test_infer_columns_mixed_type_falls_back_to_string() -> None:
    """同一字段多类型 → 回落 string（不因个别样本改变列类型）。"""
    columns = rest_api.infer_columns([{"v": 1}, {"v": "one"}])
    assert columns[0]["data_type"] == "string"


def test_infer_columns_empty_sample() -> None:
    """空样本 → 空列清单（不臆造结构）。"""
    assert rest_api.infer_columns([]) == []


async def test_probe_rest_api_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """2xx → 通过；请求带 Bearer 头。"""
    http = _Http([_Resp(200, {})])

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _Http:
            return http

        async def __aexit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(
        "etl_agent.connections.probes.rest_api_probe.httpx.AsyncClient", _Client
    )

    await probe_rest_api(
        ProbeConfig(
            connector_type="REST_API",
            host="https://api.example.com",
            port=None,
            database_name="/health",
            username="",
            password=TOKEN,
        )
    )
    assert http.calls[0]["url"] == "https://api.example.com/health"
    assert http.calls[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"


async def test_probe_rest_api_failure_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    """401 → ProbeError，摘要不含 token。"""
    http = _Http([_Resp(401, {"error": "invalid token " + TOKEN})])

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _Http:
            return http

        async def __aexit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(
        "etl_agent.connections.probes.rest_api_probe.httpx.AsyncClient", _Client
    )

    with pytest.raises(ProbeError) as excinfo:
        await probe_rest_api(
            ProbeConfig(
                connector_type="REST_API",
                host="https://api.example.com",
                port=None,
                database_name="/orders",
                username="",
                password=TOKEN,
            )
        )
    assert TOKEN not in str(excinfo.value)
