"""M-E 文件资产 — JSON / JSONL 适配器。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-E
覆盖 FR：FR-13（JSON 为需求文档点名格式；JSONL 为扩展格式）
对应 AC：AC-02（只产出内存行，脱敏由 masking 统一负责）

**内存边界**：JSONL 逐行 `json.loads`，与文件大小无关；
JSON 需整份解析后才是完整数组，故内存上界 = 文件大小上界（25 MiB，FR-13）。
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from etl_agent.files.readers.base import ReaderError, Row


class JsonReader:
    """JSON 适配器（顶层数组 → 每元素一行；顶层对象 → 单行）。"""

    def iter_rows(self, path: Path) -> Iterator[Row]:
        """按行迭代 JSON（FR-13）。

        Raises:
            ReaderError: JSON 语法错误，或顶层不是数组/对象。
        """
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                payload: Any = json.load(handle)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReaderError("JSON 解析失败") from exc
        yield from _rows_of(payload, "JSON")


class JsonLinesReader:
    """JSONL 适配器（一行一个 JSON 对象，惰性逐行）。"""

    def iter_rows(self, path: Path) -> Iterator[Row]:
        """按行迭代 JSONL（FR-13 扩展格式）。

        Raises:
            ReaderError: 某行不是合法 JSON 对象。
        """
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                for line_number, line in enumerate(handle, start=1):
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        payload: Any = json.loads(text)
                    except json.JSONDecodeError as exc:
                        raise ReaderError(f"JSONL 第 {line_number} 行不是合法 JSON") from exc
                    if not isinstance(payload, dict):
                        raise ReaderError(f"JSONL 第 {line_number} 行不是 JSON 对象")
                    yield dict(payload)
        except UnicodeDecodeError as exc:
            raise ReaderError("JSONL 不是合法的 UTF-8 文本") from exc


def _rows_of(payload: Any, label: str) -> Iterator[Row]:
    """把解析后的 JSON 载荷展开为行序列。"""
    if isinstance(payload, dict):
        yield dict(payload)
        return
    if isinstance(payload, list):
        for index, element in enumerate(payload, start=1):
            if isinstance(element, dict):
                yield dict(element)
            else:
                # 标量数组不是「记录集」，但仍应可探查：包一层固定列，避免静默丢数据
                yield {"value": element}
        return
    raise ReaderError(f"{label} 顶层必须是数组或对象，实际为 {type(payload).__name__}")
