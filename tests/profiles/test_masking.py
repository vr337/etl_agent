"""列名启发式脱敏测试（T-07.4 / FR-14，S4 提示词第十节）。

覆盖：password / email 等敏感列脱敏、非敏感列不脱敏、驱动原生类型归一化、
批量脱敏与 None 语义保持。
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from etl_agent.profiles.masking import (
    BINARY_TOKEN,
    MASK_TOKEN,
    is_sensitive_column,
    mask_row,
    mask_samples,
    mask_value,
    normalize_value,
    sensitive_columns_of,
)


def test_masking_password() -> None:
    """列名含 password / token / secret 等关键词 → 脱敏（FR-14）。"""
    assert is_sensitive_column("password") is True
    assert is_sensitive_column("user_password") is True
    assert is_sensitive_column("api_token") is True
    assert mask_value("p@ssw0rd") == MASK_TOKEN


def test_masking_email() -> None:
    """联系方式 / 证件类列名 → 脱敏（FR-14）。"""
    assert is_sensitive_column("email") is True
    assert is_sensitive_column("customer_email") is True
    assert is_sensitive_column("phone_number") is True
    assert is_sensitive_column("id_card_no") is True


def test_masking_generic() -> None:
    """非敏感列不脱敏：业务主键 / 金额 / 时间不因后缀被误伤（FR-14 误报控制）。"""
    assert is_sensitive_column("order_id") is False
    assert is_sensitive_column("amount") is False
    assert is_sensitive_column("created_at") is False
    assert is_sensitive_column("status") is False


def test_masking_personal_id_vs_business_key() -> None:
    """*id 口径：个人标识脱敏，业务主键放行（见 masking.BUSINESS_ID_PREFIXES）。"""
    assert is_sensitive_column("user_id") is True
    assert is_sensitive_column("customer_id") is True
    assert is_sensitive_column("product_id") is False
    assert is_sensitive_column("batch_id") is False


def test_masking_normalizes_driver_types() -> None:
    """驱动原生类型归一化为 JSON 安全值（否则 masked_samples 落库即失败）。"""
    assert normalize_value(Decimal("10.50")) == "10.50"
    assert normalize_value(dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.UTC)).startswith(
        "2026-10-14T12:00"
    )
    assert normalize_value(dt.date(2026, 10, 14)) == "2026-10-14"
    assert normalize_value(uuid.UUID(int=0)) == "00000000-0000-0000-0000-000000000000"
    # 二进制只回固定占位符：不暴露长度（长度本身是侧信道）
    assert normalize_value(b"\x00\x01\x02") == BINARY_TOKEN


def test_masking_keeps_none() -> None:
    """None 保持 None：不改变空值率语义，也不把「空」伪装成「已脱敏」。"""
    assert mask_value(None) is None
    row = mask_row({"password": None, "email": "a@b.com", "amount": 1}, frozenset({"email"}))
    assert row == {"password": None, "email": MASK_TOKEN, "amount": 1}


def test_mask_samples_batch() -> None:
    """批量脱敏：敏感列整体替换，非敏感列原值保留。"""
    rows = [
        {"order_id": 1, "customer_email": "alice@example.com", "amount": 10},
        {"order_id": 2, "customer_email": "bob@example.com", "amount": 20},
    ]
    columns = ["order_id", "customer_email", "amount"]
    masked = mask_samples(rows, columns)
    assert sensitive_columns_of(columns) == frozenset({"customer_email"})
    assert masked == [
        {"order_id": 1, "customer_email": MASK_TOKEN, "amount": 10},
        {"order_id": 2, "customer_email": MASK_TOKEN, "amount": 20},
    ]
    assert "alice@example.com" not in str(masked)
