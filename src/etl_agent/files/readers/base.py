"""M-E 文件资产 — 格式适配器接口与共享类型推断。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-E / 第 5.2 章 file_assets
覆盖 FR：FR-13（文件上传与探查：CSV / JSON / JSONL / XLSX / PARQUET + 扩展 ORC / XML / MARKDOWN / PDF）
对应 AC：AC-02（适配器只产出内存行，脱敏由 profiles.masking 统一负责）

**增量契约（S4 禁止事项 15）**：适配器一律以迭代器产出「一行一个 dict」，
由调用方（`files.service` / `files.probe`）边读边计数、超限即中断 ——
适配器自身不得把整份文件读进内存，否则 100,000 行上限形同虚设。
"""
from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

# 行字典的统一形态：列名 → 值（值类型由各适配器按格式原生类型给出）
Row = dict[str, Any]


class ReaderError(Exception):
    """适配器读取失败（文件损坏 / 编码错误 / 结构非法）。"""


class UnsupportedFormat(ReaderError):
    """格式不在 9 种受支持格式之内（由 service 映射为 422 FILE_FORMAT_UNSUPPORTED）。"""


@dataclass(frozen=True)
class ColumnSpec:
    """推断出的列（FR-13：Schema 推断）。"""

    name: str
    type: str
    nullable: bool


class FileReader(Protocol):
    """格式适配器协议：把文件按行迭代为字典序列。"""

    def iter_rows(self, path: Path) -> Iterator[Row]:
        """按行迭代（惰性），调用方负责计数与中断。"""
        ...


def infer_columns(rows: list[Row], column_names: list[str] | None = None) -> list[ColumnSpec]:
    """由样本行推断 Schema（FR-13）。

    Args:
        rows: 样本行（可能为空）。
        column_names: 显式列名（缺省取样本行的键并集，保序）。

    Returns:
        列规格列表；空样本且无显式列名时返回空列表。

    Note:
        类型与可空性均为**样本内推断**，不是全量扫描结论 —— 与 Profile 的
        「近似统计」同口径（FR-12/13 都不做全表扫描）。
    """
    if column_names is None:
        names: list[str] = []
        for row in rows:
            for name in row:
                if name not in names:
                    names.append(name)
    else:
        names = list(column_names)
    return [
        ColumnSpec(
            name=name,
            type=infer_type([row.get(name) for row in rows]),
            nullable=any(row.get(name) is None for row in rows),
        )
        for name in names
    ]


def infer_type(values: list[Any]) -> str:
    """由样本值推断列类型（integer / number / boolean / date / datetime / bytes / string / unknown）。"""
    present = [value for value in values if value is not None]
    if not present:
        return "unknown"
    if all(isinstance(value, bool) for value in present):
        return "boolean"
    if all(isinstance(value, int) and not isinstance(value, bool) for value in present):
        return "integer"
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in present):
        return "number"
    if all(isinstance(value, dt.datetime) for value in present):
        return "datetime"
    if all(isinstance(value, dt.date) for value in present):
        return "date"
    if all(isinstance(value, (bytes, bytearray)) for value in present):
        return "bytes"
    if all(isinstance(value, str) for value in present):
        # 文本格式（CSV / JSONL / Markdown）的值全是字符串，需按可解析性还原语义类型；
        # 判定顺序：布尔 → 时间 → 整数 → 数值（时间串不会被数值规则误吞）
        if all(_looks_like_boolean(value) for value in present):
            return "boolean"
        if all(_looks_like_datetime(value) for value in present):
            return "datetime"
        if all(_looks_like_date(value) for value in present):
            return "date"
        if all(_looks_like_integer(value) for value in present):
            return "integer"
        if all(_looks_like_number(value) for value in present):
            return "number"
        return "string"
    return "string"


def _looks_like_boolean(value: str) -> bool:
    """字符串是否为布尔字面量（true/false，大小写无关）。"""
    return value.strip().lower() in {"true", "false"}


def _looks_like_integer(value: str) -> bool:
    """字符串是否为整数（不接受小数与科学计数法）。"""
    try:
        int(value.strip())
    except ValueError:
        return False
    return True


def _looks_like_number(value: str) -> bool:
    """字符串是否为有限数值（排除 nan / inf 这类非数据值）。"""
    try:
        number = float(value.strip())
    except ValueError:
        return False
    return math.isfinite(number)


def _looks_like_datetime(value: str) -> bool:
    """字符串是否可解析为日期时间（含 ISO8601 与空格分隔两种常见写法）。"""
    if len(value) < 11:
        return False
    try:
        dt.datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _looks_like_date(value: str) -> bool:
    """字符串是否可解析为日期（YYYY-MM-DD）。"""
    if len(value) != 10:
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True
