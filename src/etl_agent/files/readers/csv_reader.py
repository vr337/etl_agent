"""M-E 文件资产 — CSV 适配器。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-E
覆盖 FR：FR-13（CSV 为需求文档点名格式）、FR-64（点名格式覆盖核对）
对应 AC：AC-02（只产出内存行，脱敏由 masking 统一负责）
"""
from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from etl_agent.files.readers.base import ReaderError, Row


class CsvReader:
    """CSV 适配器（首行为表头，惰性逐行）。"""

    def iter_rows(self, path: Path) -> Iterator[Row]:
        """按行迭代 CSV（FR-13）。

        Raises:
            ReaderError: 文件无表头或编码不可读。
        """
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames:
                    raise ReaderError("CSV 缺少表头行")
                field_names = list(reader.fieldnames)
                for raw in reader:
                    # CSV 没有原生 NULL：空单元格一律归一为 None，否则 null_rate 恒为 0
                    yield {name: _empty_to_none(raw.get(name)) for name in field_names}
        except UnicodeDecodeError as exc:
            raise ReaderError("CSV 不是合法的 UTF-8 文本") from exc


def _empty_to_none(value: str | None) -> str | None:
    """空字符串归一为 None（缺列与空单元格语义一致）。"""
    return None if value is None or value == "" else value
