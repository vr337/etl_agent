"""ClickHouse 探针（S11 T-21 / FR-05 / AC-02）。

覆盖：HTTP 接口实探成功；非 200 → ProbeError；**错误摘要不含密码**；
缺 host → 明确报错；scheme 白名单（`https` 可用、非法值回落 `http`）。
"""
from __future__ import annotations

from typing import Self

import pytest

from etl_agent.connections import clickhouse
from etl_agent.connections.probes.base import ProbeConfig, ProbeError
from etl_agent.connections.probes.clickhouse_probe import probe_clickhouse

SECRET = "clickhouse-secret-pw-9931"


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeClient:
    """httpx.AsyncClient 替身：记录请求参数，按脚本返回状态码。"""

    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> _FakeClient:  # 模拟 AsyncClient(...)
        self.kwargs = kwargs
        return self

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def post(
        self, url: str, *, params: dict[str, str], auth: tuple[str, str]
    ) -> _FakeResponse:
        self.calls.append({"url": url, "params": params, "auth": auth})
        return _FakeResponse(self.status_code)


def _config(**overrides: object) -> ProbeConfig:
    base: dict[str, object] = {
        "connector_type": "CLICKHOUSE",
        "host": "ch.local",
        "port": 8123,
        "database_name": "analytics",
        "username": "probe_user",
        "password": SECRET,
    }
    base.update(overrides)
    return ProbeConfig(**base)  # type: ignore[arg-type]


async def test_probe_clickhouse_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 接口 `SELECT 1` 成功 → 无异常，URL 与默认库按连接登记拼装。"""
    fake = _FakeClient()
    monkeypatch.setattr(
        "etl_agent.connections.probes.clickhouse_probe.httpx.AsyncClient", fake
    )

    await probe_clickhouse(_config())

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == "http://ch.local:8123/"
    assert call["params"] == {"query": "SELECT 1", "database": "analytics"}
    # 凭据走 Basic auth，绝不进 URL / 查询串（AC-02）
    assert call["auth"] == ("probe_user", SECRET)


async def test_probe_clickhouse_non_200_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 200 → ProbeError，且摘要只含 host:port 与状态码，不含密码。"""
    fake = _FakeClient(status_code=401)
    monkeypatch.setattr(
        "etl_agent.connections.probes.clickhouse_probe.httpx.AsyncClient", fake
    )

    with pytest.raises(ProbeError) as excinfo:
        await probe_clickhouse(_config())

    message = str(excinfo.value)
    assert "ch.local:8123" in message
    assert "401" in message
    assert SECRET not in message


async def test_probe_clickhouse_requires_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺 host → 明确错误（不建立任何连接）。"""
    fake = _FakeClient()
    monkeypatch.setattr(
        "etl_agent.connections.probes.clickhouse_probe.httpx.AsyncClient", fake
    )

    with pytest.raises(ProbeError):
        await probe_clickhouse(_config(host=""))
    assert fake.calls == []


async def test_probe_clickhouse_default_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """port 缺省 → 8123（HTTP 接口默认端口，不是 native 9000）。"""
    fake = _FakeClient()
    monkeypatch.setattr(
        "etl_agent.connections.probes.clickhouse_probe.httpx.AsyncClient", fake
    )

    await probe_clickhouse(_config(port=None))
    assert fake.calls[0]["url"] == "http://ch.local:8123/"


def test_clickhouse_base_url_scheme_whitelist() -> None:
    """scheme 白名单：https 放行，非法值回落 http（不把任意 scheme 拼进 URL）。"""
    assert clickhouse.clickhouse_base_url("h", 8443, scheme="https") == "https://h:8443/"
    assert clickhouse.clickhouse_base_url("h", 8123, scheme="file") == "http://h:8123/"
    assert clickhouse.clickhouse_base_url("h", None) == "http://h:8123/"


def test_clickhouse_base_url_requires_host() -> None:
    """host 为空 → ValueError（调用方应先给出可读错误）。"""
    with pytest.raises(ValueError):
        clickhouse.clickhouse_base_url("", 8123)
