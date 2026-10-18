"""M-C 数据连接 — S3 / MinIO 对象存储适配器（S11 T-21）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / M-E、第 6.1 章 API-03
覆盖 FR：FR-04（连接登记）、FR-05（连通性测试）、FR-13（文件资产来源）
对应 AC：AC-02（凭据零明文）、AC-12（项目隔离）

**为什么手写 SigV4 而不引入 boto3**（选型）：
- boto3 会连带 `botocore` / `s3transfer` / `jmespath` 等一串依赖，且是同步 SDK，
  与全异步的 httpx 栈需要额外包装；
- 本项目只需要 **ListObjectsV2 一个只读操作**，SigV4 签名约 60 行标准 HMAC 链，
  用既有 `httpx` + `hashlib` / `hmac` 即可，**零新增依赖**。

**连接参数映射**（复用 `data_connections` 现有列，不新增迁移）：
| 列 | S3 语义 |
| --- | --- |
| `host` | 端点，可带 scheme 前缀（`https://s3.amazonaws.com` / `http://127.0.0.1`） |
| `port` | 端点端口（缺省：带 `https` → 443，否则 80；MinIO 常用 9000） |
| `database_name` | 默认 bucket |
| `username` / `password` | Access Key / Secret Key（经 SecretProvider 托管） |

**region**：S3 签名必须带 region；MinIO 忽略该值。统一取 `us-east-1`（见 `DEFAULT_REGION`）。

**引擎侧凭据（FR-68）**：HOCON 只允许出现 `${S3_ACCESS_KEY}` / `${S3_SECRET_KEY}` /
`${S3_ENDPOINT}` 占位符，真实值由 Worker 在物化阶段注入环境变量，
**绝不写进 HOCON 正文或事件流**（`ENGINE_ENV_PLACEHOLDERS` 是唯一事实源）。
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

DEFAULT_REGION = "us-east-1"
DEFAULT_HTTPS_PORT = 443
DEFAULT_HTTP_PORT = 80
# ListObjectsV2 单页上限（S3 协议固定 1000）
MAX_KEYS_LIMIT = 1000
# 空请求体的 sha256（GET 请求 payload hash）
EMPTY_PAYLOAD_SHA256 = hashlib.sha256(b"").hexdigest()

# 引擎侧占位符（FR-68：HOCON 里只有这些名字，真实值由 Worker 注入）
ENGINE_ENV_PLACEHOLDERS = {
    "endpoint": "${S3_ENDPOINT}",
    "access_key": "${S3_ACCESS_KEY}",
    "secret_key": "${S3_SECRET_KEY}",
    "bucket": "${S3_BUCKET}",
}


@dataclass(frozen=True)
class S3Endpoint:
    """S3 端点定位（path-style 寻址：`{scheme}://{host}:{port}/{bucket}`）。"""

    scheme: str
    host: str
    port: int
    bucket: str

    @property
    def base_url(self) -> str:
        """bucket 级基址（不含尾斜杠；默认端口省略，与 Host 头保持同形）。"""
        return f"{self.scheme}://{self.host_header}/{self.bucket}"

    @property
    def host_header(self) -> str:
        """Host 头：默认端口不带端口号（SigV4 的 canonical headers 依赖此形态）。"""
        default_port = DEFAULT_HTTPS_PORT if self.scheme == "https" else DEFAULT_HTTP_PORT
        return self.host if self.port == default_port else f"{self.host}:{self.port}"


@dataclass(frozen=True)
class S3Object:
    """对象元数据（只读列举，不含对象正文）。"""

    key: str
    size: int
    etag: str | None
    last_modified: str | None


class S3ListError(Exception):
    """对象列举失败（消息必须脱敏：只含端点与状态码）。"""


def parse_endpoint(host: str, port: int | None, bucket: str | None) -> S3Endpoint:
    """从连接登记参数解析端点（`host` 可带 scheme 前缀）。

    Args:
        host: 端点主机，允许 `https://s3.amazonaws.com` 这种带 scheme 的写法。
        port: 端点端口；缺省按 scheme 取 443 / 80。
        bucket: 默认 bucket（连接登记 `database_name`）。

    Raises:
        ValueError: host 为空或 bucket 缺失（调用方应先给出可读错误）。
    """
    if not host:
        raise ValueError("S3 定位参数缺少 host")
    if not bucket:
        raise ValueError("S3 定位参数缺少 bucket（连接登记的 database_name）")

    raw = host.strip()
    if "://" in raw:
        parts = urlsplit(raw)
        scheme = parts.scheme or "http"
        resolved_host = parts.hostname or ""
        scheme_port = parts.port
    else:
        scheme = "http"
        resolved_host = raw
        scheme_port = None
    if scheme not in ("http", "https"):
        raise ValueError(f"不支持的 S3 端点 scheme: {scheme!r}")

    default_port = DEFAULT_HTTPS_PORT if scheme == "https" else DEFAULT_HTTP_PORT
    effective_port = port or scheme_port or default_port
    return S3Endpoint(
        scheme=scheme, host=resolved_host, port=effective_port, bucket=bucket
    )


def engine_s3_uri(bucket: str, key: str) -> str:
    """引擎侧对象路径（`s3://bucket/key`），不含任何凭据。"""
    return f"s3://{bucket}/{key.lstrip('/')}"


# ============================================================
# SigV4 签名（AWS Signature Version 4，仅 GET + 无 body 的只读场景）
# ============================================================


def _hmac_sha256(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _canonical_query(params: dict[str, str]) -> str:
    """按 RFC 3986 编码 + 键名排序拼 canonical query string。"""
    pairs = sorted(
        (quote(str(key), safe="-_.~"), quote(str(value), safe="-_.~"))
        for key, value in params.items()
    )
    return "&".join(f"{key}={value}" for key, value in pairs)


def sign_request(
    *,
    endpoint: S3Endpoint,
    access_key: str,
    secret_key: str,
    params: dict[str, str],
    region: str = DEFAULT_REGION,
    now: datetime | None = None,
) -> dict[str, str]:
    """为 `GET /{bucket}?<params>` 生成 SigV4 认证头。

    Args:
        endpoint: 端点（提供 host 头与 canonical URI）。
        access_key / secret_key: 受管凭据（内存中短暂持有）。
        params: 查询参数（ListObjectsV2 的 `list-type` / `prefix` 等）。
        region: 签名区域（MinIO 忽略）。
        now: 注入时钟（测试用；缺省取当前 UTC）。

    Returns:
        需要附加到请求上的头（`Authorization` / `x-amz-date` / `x-amz-content-sha256`）。
    """
    moment = now or datetime.now(UTC)
    amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = moment.strftime("%Y%m%d")

    canonical_uri = f"/{endpoint.bucket}"
    canonical_query = _canonical_query(params)
    canonical_headers = f"host:{endpoint.host_header}\nx-amz-content-sha256:{EMPTY_PAYLOAD_SHA256}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    # 规范请求：canonical_headers 自带尾换行，故 headers 与 signed_headers 之间
    # 会形成一个空行 —— 这是 SigV4 规范要求的形态，不要「顺手」去掉。
    canonical_request = (
        f"GET\n{canonical_uri}\n{canonical_query}\n{canonical_headers}\n"
        f"{signed_headers}\n{EMPTY_PAYLOAD_SHA256}"
    )

    scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    signing_key = _hmac_sha256(
        _hmac_sha256(
            _hmac_sha256(_hmac_sha256(f"AWS4{secret_key}".encode(), date_stamp), region), "s3"
        ),
        "aws4_request",
    )
    signature = hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return {
        "Authorization": authorization,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": EMPTY_PAYLOAD_SHA256,
    }


# ============================================================
# ListObjectsV2（只读前缀列举）
# ============================================================


async def list_objects_v2(
    endpoint: S3Endpoint,
    credentials: tuple[str, str],
    *,
    prefix: str = "",
    max_keys: int = 100,
    continuation_token: str | None = None,
    region: str = DEFAULT_REGION,
    client: Any | None = None,
) -> tuple[list[S3Object], str | None]:
    """列举 bucket 下指定前缀的对象（`GET ?list-type=2`）。

    Args:
        endpoint: 端点定位。
        credentials: `(access_key, secret_key)`，只在内存中短暂持有。
        prefix: 对象键前缀（空串 = 根）。
        max_keys: 单页条数（1–1000）。
        continuation_token: 续页令牌（来自上一页响应）。
        region: 签名区域。
        client: 注入的 httpx.AsyncClient（测试用；缺省自建并在结束时关闭）。

    Returns:
        `(objects, next_token)`；`next_token` 为 None 表示已到末页。

    Raises:
        S3ListError: 非 2xx（消息只含状态码，不回传响应正文）。
    """
    import httpx

    params: dict[str, str] = {"list-type": "2", "max-keys": str(max(1, min(max_keys, MAX_KEYS_LIMIT)))}
    if prefix:
        params["prefix"] = prefix
    if continuation_token:
        params["continuation-token"] = continuation_token

    headers = sign_request(
        endpoint=endpoint,
        access_key=credentials[0],
        secret_key=credentials[1],
        params=params,
        region=region,
    )

    owned = client is None
    http = client or httpx.AsyncClient(timeout=10)
    try:
        response = await http.get(endpoint.base_url, params=params, headers=headers)
    finally:
        if owned:
            await http.aclose()

    if response.status_code != httpx.codes.OK:
        raise S3ListError(
            f"s3 list failed at {endpoint.host}:{endpoint.port} (HTTP {response.status_code})"
        )
    return _parse_list_response(response.text)




def _parse_list_response(body: str) -> tuple[list[S3Object], str | None]:
    """解析 ListObjectsV2 的 XML 响应（含 `IsTruncated` 与续页令牌）。"""
    from xml.etree import ElementTree

    # S3 的 XML 带默认命名空间；按后缀匹配，避免命名空间前缀差异
    root = ElementTree.fromstring(body)
    contents = root.findall("{*}Contents")
    objects = [
        S3Object(
            key=(item.findtext("{*}Key") or ""),
            size=int(item.findtext("{*}Size") or 0),
            etag=item.findtext("{*}ETag"),
            last_modified=item.findtext("{*}LastModified"),
        )
        for item in contents
    ]
    truncated = (root.findtext("{*}IsTruncated") or "").strip().lower() == "true"
    next_token = root.findtext("{*}NextContinuationToken")
    return objects, (next_token if truncated and next_token else None)
