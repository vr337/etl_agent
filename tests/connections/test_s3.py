"""S3 / MinIO 适配器（S11 T-21 / FR-05 / FR-13 / AC-02）。

覆盖：端点解析（scheme / 默认端口 / 缺 bucket）；SigV4 签名结构与确定性；
ListObjectsV2 的 XML 解析与续页令牌；失败摘要脱敏；引擎路径与占位符不含凭据。
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from etl_agent.connections import s3
from etl_agent.connections.probes.base import ProbeConfig, ProbeError
from etl_agent.connections.probes.s3_probe import probe_s3

AK = "AKIAIOSFODNN7EXAMPLE"
SK = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

LIST_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>demo-bucket</Name>
  <Prefix>raw/</Prefix>
  <IsTruncated>true</IsTruncated>
  <NextContinuationToken>tok-2</NextContinuationToken>
  <Contents>
    <Key>raw/orders.csv</Key>
    <LastModified>2026-10-17T00:00:00.000Z</LastModified>
    <ETag>&quot;abc123&quot;</ETag>
    <Size>1024</Size>
  </Contents>
</ListBucketResult>
"""


def test_parse_endpoint_with_scheme() -> None:
    """host 可带 scheme 前缀；默认端口按 scheme 取 443。"""
    endpoint = s3.parse_endpoint("https://s3.amazonaws.com", None, "demo-bucket")
    assert endpoint.scheme == "https"
    assert endpoint.host == "s3.amazonaws.com"
    assert endpoint.port == 443
    assert endpoint.base_url == "https://s3.amazonaws.com/demo-bucket"


def test_parse_endpoint_minio_path_style() -> None:
    """MinIO 常用 http + 9000，base_url 必须带端口（非默认端口）。"""
    endpoint = s3.parse_endpoint("http://127.0.0.1", 9000, "raw")
    assert endpoint.base_url == "http://127.0.0.1:9000/raw"
    assert endpoint.host_header == "127.0.0.1:9000"


def test_parse_endpoint_requires_bucket() -> None:
    """缺 bucket（database_name）→ ValueError（调用方转 422）。"""
    with pytest.raises(ValueError):
        s3.parse_endpoint("s3.amazonaws.com", None, None)
    with pytest.raises(ValueError):
        s3.parse_endpoint("", 9000, "raw")


def test_sign_request_structure_and_determinism() -> None:
    """SigV4 头结构正确、同输入签名稳定、不同密钥签名不同。"""
    endpoint = s3.parse_endpoint("https://s3.amazonaws.com", None, "demo-bucket")
    fixed = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)
    params = {"list-type": "2", "max-keys": "1"}

    headers = s3.sign_request(
        endpoint=endpoint, access_key=AK, secret_key=SK, params=params, now=fixed
    )
    authorization = headers["Authorization"]
    assert authorization.startswith("AWS4-HMAC-SHA256 Credential=")
    assert f"{AK}/20261017/us-east-1/s3/aws4_request" in authorization
    assert "SignedHeaders=host;x-amz-content-sha256;x-amz-date" in authorization
    assert headers["x-amz-date"] == "20261017T120000Z"
    assert headers["x-amz-content-sha256"] == s3.EMPTY_PAYLOAD_SHA256

    again = s3.sign_request(
        endpoint=endpoint, access_key=AK, secret_key=SK, params=params, now=fixed
    )
    assert again["Authorization"] == authorization

    other = s3.sign_request(
        endpoint=endpoint, access_key=AK, secret_key="another-secret", params=params, now=fixed
    )
    assert other["Authorization"] != authorization


def test_sign_request_query_is_sorted_and_encoded() -> None:
    """canonical query 必须按键名排序（`continuation-token` 在 `list-type` 前）。"""
    endpoint = s3.parse_endpoint("https://s3.amazonaws.com", None, "demo-bucket")
    fixed = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)
    base = s3.sign_request(
        endpoint=endpoint,
        access_key=AK,
        secret_key=SK,
        params={"list-type": "2", "prefix": "raw/"},
        now=fixed,
    )
    reordered = s3.sign_request(
        endpoint=endpoint,
        access_key=AK,
        secret_key=SK,
        params={"prefix": "raw/", "list-type": "2"},
        now=fixed,
    )
    assert base["Authorization"] == reordered["Authorization"]


class _Response:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeHttp:
    def __init__(self, response: _Response) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    async def get(
        self, url: str, *, params: dict[str, str], headers: dict[str, str]
    ) -> _Response:
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self._response

    async def aclose(self) -> None:
        return None


async def test_list_objects_v2_parses_and_pages() -> None:
    """解析 Contents / IsTruncated / NextContinuationToken，并带上签名头。"""
    http = _FakeHttp(_Response(200, LIST_XML))
    endpoint = s3.parse_endpoint("https://s3.amazonaws.com", None, "demo-bucket")

    objects, next_token = await s3.list_objects_v2(
        endpoint, (AK, SK), prefix="raw/", max_keys=10, client=http
    )

    assert [item.key for item in objects] == ["raw/orders.csv"]
    assert objects[0].size == 1024
    assert next_token == "tok-2"
    call = http.calls[0]
    assert call["params"]["list-type"] == "2"
    assert call["params"]["prefix"] == "raw/"
    assert call["params"]["max-keys"] == "10"
    assert "Authorization" in call["headers"]  # type: ignore[operator]


async def test_list_objects_v2_error_masked() -> None:
    """非 2xx → S3ListError，摘要只含端点与状态码，不含密钥。"""
    http = _FakeHttp(_Response(403, "AccessDenied: secret=" + SK))
    endpoint = s3.parse_endpoint("http://minio.local", 9000, "raw")

    with pytest.raises(s3.S3ListError) as excinfo:
        await s3.list_objects_v2(endpoint, (AK, SK), client=http)

    message = str(excinfo.value)
    assert "minio.local:9000" in message
    assert "403" in message
    assert SK not in message


def test_engine_path_and_placeholders_have_no_credentials() -> None:
    """引擎侧路径与占位符**不含任何凭据**（FR-68：HOCON 只有占位符）。"""
    uri = s3.engine_s3_uri("demo-bucket", "/raw/orders.csv")
    assert uri == "s3://demo-bucket/raw/orders.csv"
    assert AK not in uri and SK not in uri

    for value in s3.ENGINE_ENV_PLACEHOLDERS.values():
        assert value.startswith("${") and value.endswith("}")
        assert AK not in value and SK not in value


class _FakeProbeClient:
    """探针用：替换 s3_probe.list_objects_v2 的替身（记录调用参数）。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def __call__(self, endpoint: object, credentials: object, **kwargs: object) -> tuple:
        self.calls.append({"endpoint": endpoint, "credentials": credentials, **kwargs})
        if self.error is not None:
            raise self.error
        return [], None


async def test_probe_s3_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """ListObjectsV2 成功 → 无异常，max_keys=1（只验连通，不搬正文）。"""
    fake = _FakeProbeClient()
    monkeypatch.setattr("etl_agent.connections.probes.s3_probe.list_objects_v2", fake)

    await probe_s3(
        ProbeConfig(
            connector_type="S3",
            host="https://s3.amazonaws.com",
            port=None,
            database_name="demo-bucket",
            username=AK,
            password=SK,
        )
    )
    assert fake.calls[0]["max_keys"] == 1
    assert fake.calls[0]["credentials"] == (AK, SK)


async def test_probe_s3_failure_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    """列举失败 → ProbeError，摘要不含密钥。"""
    fake = _FakeProbeClient(error=s3.S3ListError("s3 list failed at h:1 (HTTP 403)"))
    monkeypatch.setattr("etl_agent.connections.probes.s3_probe.list_objects_v2", fake)

    with pytest.raises(ProbeError) as excinfo:
        await probe_s3(
            ProbeConfig(
                connector_type="S3",
                host="h",
                port=1,
                database_name="b",
                username=AK,
                password=SK,
            )
        )
    assert SK not in str(excinfo.value)
