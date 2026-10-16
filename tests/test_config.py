"""Settings.host_map 字段用例（T-ENGINE-02 缺陷 2：执行视角 host 映射的配置面）。

契约：env `ETL_AGENT_HOST_MAP` 为 JSON 对象；合法值正确解析、非法值启动期
fail-fast（不静默吞——坏配置比没配置更危险）；缺省空 dict = 不重写。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsError

from etl_agent.config import Settings


def test_host_map_defaults_to_empty() -> None:
    """缺省空：不透传/不重写（未部署映射的环境零影响）。"""
    assert Settings().host_map == {}


def test_host_map_parses_json_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """合法 JSON：env 字符串 → dict（pydantic-settings 复杂字段解析）。"""
    monkeypatch.setenv(
        "ETL_AGENT_HOST_MAP",
        '{"127.0.0.1:13306": "mysql:3306", "127.0.0.1:9030": "doris-fe:9030"}',
    )
    settings = Settings()
    assert settings.host_map["127.0.0.1:13306"] == "mysql:3306"
    assert settings.host_map["127.0.0.1:9030"] == "doris-fe:9030"


def test_host_map_accepts_dict_directly() -> None:
    """代码内直传 dict（测试/内嵌场景）同样生效。"""
    settings = Settings(host_map={"127.0.0.1:13306": "mysql:3306"})
    assert settings.host_map == {"127.0.0.1:13306": "mysql:3306"}


def test_host_map_invalid_json_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """非法 JSON：启动期即炸（ValidationError / SettingsError），不静默吞。"""
    monkeypatch.setenv("ETL_AGENT_HOST_MAP", "{127.0.0.1:13306: mysql:3306}")
    with pytest.raises((ValidationError, SettingsError)):
        Settings()
