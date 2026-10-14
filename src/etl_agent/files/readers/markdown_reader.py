"""M-E 文件资产 — Markdown 适配器。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-E
覆盖 FR：FR-13（Markdown 为扩展格式）
对应 AC：AC-02（只产出内存行，脱敏由 masking 统一负责）

**行语义**：优先把 Markdown 表格（表头 + 分隔行 + 数据行）抽为记录集；
全文不含表格时退化为「一行一条文本」（列名固定为 `text`），避免探查结果为空。

**内存边界**：表格与正文的判定需要「先知道全文有没有表格」，故本适配器
先整份读入（上界 = 文件大小上界 25 MiB，FR-13），再逐行产出。
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from etl_agent.files.readers.base import ReaderError, Row

_SEPARATOR_CELL = re.compile(r"^:?-{1,}:?$")


class MarkdownReader:
    """Markdown 适配器（表格优先，退化为一列文本）。"""

    def iter_rows(self, path: Path) -> Iterator[Row]:
        """按行迭代 Markdown（FR-13 扩展格式）。

        Raises:
            ReaderError: 文件不是合法的 UTF-8 文本。
        """
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except UnicodeDecodeError as exc:
            raise ReaderError("Markdown 不是合法的 UTF-8 文本") from exc

        records = list(_table_records(lines))
        if records:
            yield from records
            return
        for line in lines:
            text = line.strip()
            if text:
                yield {"text": text}


def _table_records(lines: list[str]) -> Iterator[Row]:
    """抽取全部 Markdown 表格的记录行。"""
    index = 0
    total = len(lines)
    while index < total:
        header = _cells(lines[index])
        if header is not None and index + 1 < total and _is_separator(lines[index + 1]):
            index += 2
            while index < total:
                if _is_separator(lines[index]):
                    index += 1
                    continue
                cells = _cells(lines[index])
                if cells is None:
                    break
                yield {
                    name: (cells[position] if position < len(cells) else None)
                    for position, name in enumerate(header)
                }
                index += 1
            continue
        index += 1


def _cells(line: str) -> list[str] | None:
    """表格行 → 单元格列表（非表格行返回 None）。"""
    stripped = line.strip()
    if "|" not in stripped:
        return None
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _is_separator(line: str) -> bool:
    """是否为表格分隔行（如 |---|---|）。"""
    cells = _cells(line)
    if not cells:
        return False
    return all(_SEPARATOR_CELL.match(cell) for cell in cells)
