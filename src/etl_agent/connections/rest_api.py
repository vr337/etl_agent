"""M-C 数据连接 — REST API 数据源适配器（S11 T-21）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / 第 6.1 章 API-03
覆盖 FR：FR-04（连接登记）、FR-05（连通性测试）、FR-12（表级只读 Profile）
对应 AC：AC-02（凭据零明文 / 失败摘要脱敏）、AC-12（项目隔离）

**为什么只做「拉取契约 + 认证 + Schema 推断」，不接生成引擎**：
REST 源的落地需要 EtlPlan 扩展（`Http` 连接器块、分页参数物化、增量水位），
属契约变更（AMEND 级），本轮按人类给定的 P2 优先级先交付**可登记 / 可探针 /
可分页拉取 / 可冻结 Schema** 四件事，HOCON 接线另行排期。

**连接参数映射**（复用 `data_connections` 现有列，不新增迁移）：
| 列 | REST 语义 |
| --- | --- |
| `host` | Base URL（`https://api.example.com` 或 `https://api.example.com/v1`） |
| `port` | 端口（Base URL 自带端口时以 URL 为准） |
| `database_name` | 资源路径（如 `/orders`），可空（空则请求 Base URL 本身） |
| `username` / `password` | 认证：`username` 空 → Bearer `password`；非空 → Basic |

**认证头受管化（任务要求）**：token / 口令只从 SecretProvider 解密到内存，
再由本模块拼进请求头；**绝不写进连接记录、日志或任何响应体**。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

# 分页模式（任务要求的「拉取契约」）
PAGINATION_NONE = "NONE"
PAGINATION_PAGE = "PAGE"
PAGINATION_OFFSET = "OFFSET"
PAGINATION_CURSOR = "CURSOR"
PAGINATION_MODES = frozenset(
    {PAGINATION_NONE, PAGINATION_PAGE, PAGINATION_OFFSET, PAGINATION_CURSOR}
)

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 1000


class RestApiError(Exception):
    """REST 拉取失败（消息必须脱敏：只含端点与状态码）。"""


@dataclass(frozen=True)
class RestEndpoint:
    """REST 端点定位（base_url + 资源路径）。"""

    base_url: str
    path: str = ""

    @property
    def url(self) -> str:
        """完整请求 URL（path 为空时即 base_url）。"""
        if not self.path:
            return self.base_url
        return self.base_url.rstrip("/") + "/" + self.path.lstrip("/")

    @property
    def host_label(self) -> str:
        """脱敏用的端点标签（`host:port`，不含路径与查询串）。"""
        parts = urlsplit(self.base_url)
        return f"{parts.hostname or ''}:{parts.port or (443 if parts.scheme == 'https' else 80)}"


@dataclass(frozen=True)
class FetchSpec:
    """拉取契约：分页方式 + 参数名 + 取数位置。

    四种模式与参数名全部显式声明，不猜服务端约定：
    - `NONE`：单次拉取，无分页参数；
    - `PAGE`：`?{page_param}=N&{size_param}=M`，N 从 1 起；
    - `OFFSET`：`?{offset_param}=N&{size_param}=M`；
    - `CURSOR`：首屏不带游标，后续带 `?{cursor_param}=<token>`，
      下一页 token 从响应 JSON 的 `cursor_field`（支持 `a.b` 点路径）取。
    """

    pagination: str = PAGINATION_NONE
    page_param: str = "page"
    offset_param: str = "offset"
    size_param: str = "size"
    cursor_param: str = "cursor"
    cursor_field: str = "next_cursor"
    items_field: str | None = None
    page_size: int = DEFAULT_PAGE_SIZE
    start_page: int = 1
    extra_params: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.pagination not in PAGINATION_MODES:
            raise ValueError(
                f"不支持的分页模式: {self.pagination!r}，可选 {sorted(PAGINATION_MODES)}"
            )
        if not 1 <= self.page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size 必须在 1–{MAX_PAGE_SIZE} 之间")


@dataclass(frozen=True)
class Page:
    """一页数据 + 下一页游标（None 表示没有下一页）。"""

    items: list[dict[str, Any]]
    next_cursor: str | None = None


def parse_endpoint(host: str, port: int | None, path: str | None) -> RestEndpoint:
    """解析 Base URL（补 scheme / 端口）与资源路径。

    Raises:
        ValueError: host 为空或不是 http(s) URL。
    """
    if not host:
        raise ValueError("REST 定位参数缺少 host（Base URL）")
    raw = host.strip()
    if "://" not in raw:
        raw = f"https://{raw}"
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"不支持的 REST scheme: {parts.scheme!r}")
    netloc = parts.netloc
    if port and parts.port is None:
        netloc = f"{netloc}:{port}"
    base = urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", ""))
    return RestEndpoint(base_url=base, path=(path or "").strip())


def auth_headers(credentials: dict[str, str]) -> dict[str, str]:
    """按凭据形态生成认证头（**只在内存中构造，不落任何持久化**）。

    - 无 `password` → 不带认证头（公开 API；受保护端点会在 401 处明确失败）
    - `username` 为空 → `Authorization: Bearer <password>`
    - `username` 非空 → `Authorization: Basic base64(username:password)`
    """
    import base64

    username = (credentials.get("username") or "").strip()
    password = credentials.get("password") or ""
    if not password:
        return {}
    if not username:
        return {"Authorization": f"Bearer {password}"}
    raw = f"{username}:{password}".encode()
    return {"Authorization": f"Basic {base64.b64encode(raw).decode('ascii')}"}


def build_params(spec: FetchSpec, cursor: str | None, page_index: int) -> dict[str, str]:
    """按契约构造查询参数（`page_index` 从 0 起，PAGE 模式按 `start_page` 偏移）。"""
    params: dict[str, str] = dict(spec.extra_params)
    if spec.pagination == PAGINATION_PAGE:
        params[spec.page_param] = str(spec.start_page + page_index)
        params[spec.size_param] = str(spec.page_size)
    elif spec.pagination == PAGINATION_OFFSET:
        params[spec.offset_param] = str(page_index * spec.page_size)
        params[spec.size_param] = str(spec.page_size)
    elif spec.pagination == PAGINATION_CURSOR and cursor:
        params[spec.cursor_param] = cursor
        params[spec.size_param] = str(spec.page_size)
    elif spec.pagination == PAGINATION_CURSOR:
        params[spec.size_param] = str(spec.page_size)
    return params


async def fetch_page(
    endpoint: RestEndpoint,
    credentials: dict[str, str],
    spec: FetchSpec,
    *,
    cursor: str | None = None,
    page_index: int = 0,
    client: Any | None = None,
) -> Page:
    """拉取一页（2xx 才算成功；非 2xx 抛 RestApiError，摘要不含响应正文）。

    Args:
        endpoint: 端点定位。
        credentials: SecretProvider 解密出的凭据（内存中短暂持有）。
        spec: 拉取契约。
        cursor: 游标（仅 CURSOR 模式使用）。
        page_index: 页序号（从 0 起；PAGE 模式叠加 `start_page`）。
        client: 注入的 httpx.AsyncClient（测试用；缺省自建并关闭）。
    """
    import httpx

    headers = auth_headers(credentials)
    params = build_params(spec, cursor, page_index)

    owned = client is None
    http = client or httpx.AsyncClient(timeout=15)
    try:
        response = await http.get(endpoint.url, params=params, headers=headers)
    finally:
        if owned:
            await http.aclose()

    if response.status_code < 200 or response.status_code >= 300:
        raise RestApiError(
            f"rest fetch failed at {endpoint.host_label} (HTTP {response.status_code})"
        )

    payload = response.json()
    items = _extract_items(payload, spec.items_field)
    next_cursor = None
    if spec.pagination == PAGINATION_CURSOR:
        next_cursor = _dig(payload, spec.cursor_field)
    return Page(items=items, next_cursor=next_cursor)


def _extract_items(payload: Any, items_field: str | None) -> list[dict[str, Any]]:
    """从响应体取记录数组（根数组或 `items_field` 点路径）。"""
    node: Any = payload if items_field is None else _dig_node(payload, items_field)
    if not isinstance(node, list):
        raise RestApiError("REST 响应不是记录数组（请检查 items_field 配置）")
    return [item for item in node if isinstance(item, dict)]


def _dig(payload: Any, dotted: str) -> str | None:
    """按点路径取字符串值（取不到或非字符串 → None，表示没有下一页）。"""
    node = _dig_node(payload, dotted)
    if isinstance(node, str) and node:
        return node
    return None


def _dig_node(payload: Any, dotted: str) -> Any:
    node: Any = payload
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


async def iter_pages(
    endpoint: RestEndpoint,
    credentials: dict[str, str],
    spec: FetchSpec,
    *,
    max_pages: int = 100,
    client: Any | None = None,
) -> list[Page]:
    """按契约翻页（有界：`max_pages` 防止服务端游标不前进导致死循环）。

    终止条件：本页为空 / 无下一页游标 / 达到 `max_pages`。
    """
    pages: list[Page] = []
    cursor: str | None = None
    for index in range(max_pages):
        page = await fetch_page(
            endpoint, credentials, spec, cursor=cursor, page_index=index, client=client
        )
        pages.append(page)
        if not page.items:
            break
        if spec.pagination == PAGINATION_NONE:
            break
        if spec.pagination == PAGINATION_CURSOR:
            if not page.next_cursor or page.next_cursor == cursor:
                break
            cursor = page.next_cursor
        elif len(page.items) < spec.page_size:
            break
    return pages


# ============================================================
# 响应 Schema 冻结（任务要求：把响应结构固化为 Profile）
# ============================================================

# JSON 类型 → 列类型（消费方按此建 Profile；不做过度推断）
_JSON_TYPE_MAP: dict[type, str] = {
    str: "string",
    bool: "boolean",
    int: "bigint",
    float: "double",
}


def infer_columns(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """由样本记录推断列结构（name / data_type / nullable）。

    - 字段名取**所有样本的并集**（不同记录字段可能不一致）；
    - 同一字段出现多种类型时回落 `string`（保守：不因个别样本改变列类型）；
    - 任一记录缺该字段或值为 null → `nullable=True`；
    - 嵌套对象/数组标记为 `json`（不展开，避免把结构推断成业务契约）。
    """
    if not items:
        return []
    names: list[str] = []
    for item in items:
        for name in item:
            if name not in names:
                names.append(name)

    columns: list[dict[str, Any]] = []
    for name in names:
        observed: set[str] = set()
        nullable = False
        for item in items:
            if name not in item or item[name] is None:
                nullable = True
                continue
            value = item[name]
            if isinstance(value, (dict, list)):
                observed.add("json")
            else:
                observed.add(_JSON_TYPE_MAP.get(type(value), "string"))
        data_type = observed.pop() if len(observed) == 1 else "string"
        columns.append({"name": name, "data_type": data_type, "nullable": nullable})
    return columns
