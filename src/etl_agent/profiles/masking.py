"""M-D 元数据探查与数据分级 — 列名启发式脱敏。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-D / 第 8 章「安全 · 凭据保护」行
覆盖 FR：FR-14（脱敏样本渲染：样本值不回明文）、FR-13（文件探查复用同一策略）
对应 AC：AC-02（全链路无敏感明文泄露）

脱敏策略由本模块统一实现，profiles 与 files 两个域共用（S4 提示词 9.4：不重写）。

**脱敏不可逆**：命中即整体替换为固定占位符，不保留长度 / 前缀 / 哈希等任何可逆线索，
避免「脱敏后仍可枚举还原」。方向选择「宁可误伤」——与 S3 probe.mask_error 同源
（PRD 10.5：全链路敏感明文泄露 = 0）。

同时承担**值归一化**：驱动原生类型（`Decimal` / `datetime` / `UUID` / 二进制）
不能直接进 JSON 列，故在落 `masked_samples` 前统一转为 JSON 安全表示。
"""
from __future__ import annotations

import datetime as dt
import decimal
import uuid
from typing import Any

# 脱敏占位符：固定值，不携带长度信息（长度本身也是侧信道）
MASK_TOKEN = "***"

# 二进制值的占位符：同样不暴露长度（长度可反推业务含义，属侧信道）
BINARY_TOKEN = "<binary>"

# 关键词命中（子串匹配，大小写无关）：覆盖 password / token / 联系方式 / 证件 / 账户
SENSITIVE_KEYWORDS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "credential",
        "api_key",
        "apikey",
        "access_key",
        "phone",
        "mobile",
        "telephone",
        "email",
        "mail",
        "address",
        "addr",
        "ssn",
        "passport",
        "idcard",
        "id_card",
        "national_id",
        "identity",
        "certificate",
        "cert_no",
        "bank",
        "card",
        "account",
    }
)

# *name 后缀：username / full_name / customer_name / real_name
SENSITIVE_SUFFIXES = ("name",)

# *id 后缀的个人标识前缀：user_id / customer_id / employee_id 等属个人标识，脱敏
# 例外：业务主键（order_id / product_id / batch_id…）不指向自然人，脱敏会毁掉
# Profile 的可用性而不增加安全性，故不脱敏（S4 提示词 9.1 的「*id」按此口径落地）。
BUSINESS_ID_PREFIXES = frozenset(
    {
        "order",
        "product",
        "item",
        "sku",
        "invoice",
        "batch",
        "job",
        "task",
        "run",
        "pipeline",
        "version",
        "asset",
        "profile",
        "connection",
        "project",
        "tenant",
        "seq",
        "num",
        "no",
    }
)


def normalize_column_name(column_name: str) -> str:
    """列名归一化（去空白 + 小写），用于启发式匹配。"""
    return column_name.strip().lower()


def is_sensitive_column(column_name: str) -> bool:
    """列名启发式判定：是否为敏感列（命中即脱敏）。

    Args:
        column_name: 原始列名（任意大小写 / 分隔符）。

    Returns:
        True 表示该列样本值必须脱敏。
    """
    name = normalize_column_name(column_name)
    if not name:
        return False
    if any(keyword in name for keyword in SENSITIVE_KEYWORDS):
        return True
    if any(name.endswith(suffix) for suffix in SENSITIVE_SUFFIXES):
        return True
    # *id：仅个人标识前缀脱敏，业务主键放行（见 BUSINESS_ID_PREFIXES 注释）
    if name.endswith("id"):
        prefix = name[:-2].rstrip("_")
        return prefix not in BUSINESS_ID_PREFIXES
    return False


def mask_value(value: Any) -> Any:
    """单值脱敏：None 保持 None（不改变空值率语义），其余一律替换为占位符。"""
    if value is None:
        return None
    return MASK_TOKEN


def normalize_value(value: Any) -> Any:
    """把驱动原生类型转为 JSON 可序列化表示（非脱敏列的样本值）。

    `masked_samples` 是 JSON 列，asyncpg/aiomysql 会给出 `Decimal`、`datetime`、
    `UUID`、`bytes` 等 JSON 编码器不认识的类型 —— 不归一化会在落库时报错。

    Returns:
        JSON 安全值；二进制只回固定占位符（长度属侧信道，见 BINARY_TOKEN）。
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, decimal.Decimal):
        # 用字符串保留精度：转 float 会在大额数值上静默丢有效位
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return BINARY_TOKEN
    if isinstance(value, dict):
        return {str(key): normalize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [normalize_value(item) for item in value]
    return str(value)


def mask_row(row: dict[str, Any], sensitive_columns: frozenset[str]) -> dict[str, Any]:
    """按敏感列集合脱敏单行样本（键名原样保留；非敏感列做 JSON 归一化）。"""
    masked: dict[str, Any] = {}
    for key, value in row.items():
        if key in sensitive_columns:
            masked[key] = mask_value(value)
        else:
            masked[key] = normalize_value(value)
    return masked


def sensitive_columns_of(column_names: list[str]) -> frozenset[str]:
    """筛选出敏感列名集合（供批量脱敏复用，避免逐行重复判定）。"""
    return frozenset(name for name in column_names if is_sensitive_column(name))


def mask_samples(
    rows: list[dict[str, Any]], column_names: list[str]
) -> list[dict[str, Any]]:
    """批量脱敏样本行（FR-14：Profile 与文件探查共用的唯一出口）。

    Args:
        rows: 原始样本行（调用方保证只在内存中短暂持有，禁止落库/日志）。
        column_names: 该批样本的列名全集。

    Returns:
        脱敏后的样本行，可直接落 `data_profiles.masked_samples`（AC-02）。
    """
    sensitive = sensitive_columns_of(column_names)
    return [mask_row(row, sensitive) for row in rows]
