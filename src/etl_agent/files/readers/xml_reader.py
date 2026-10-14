"""M-E 文件资产 — XML 适配器。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-E
覆盖 FR：FR-13（XML 为扩展格式）
对应 AC：AC-02（只产出内存行，脱敏由 masking 统一负责）

**行语义**：根元素的**直接子元素**各视为一条记录；属性以 `@名称` 入列，
子元素以标签名入列（嵌套子元素取其全部文本）。用 `iterparse` 边解析边产出
并 `clear()` 已消费子树，内存与「记录数」成正比而非与文件大小成正比。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path

from etl_agent.files.readers.base import ReaderError, Row


class XmlReader:
    """XML 适配器（根元素直接子元素 = 记录）。"""

    def iter_rows(self, path: Path) -> Iterator[Row]:
        """按记录迭代 XML（FR-13 扩展格式）。

        Raises:
            ReaderError: XML 语法错误或文件为空。
        """
        try:
            context = ET.iterparse(str(path), events=("start", "end"))
            _, root = next(context)
        except (ET.ParseError, StopIteration) as exc:
            raise ReaderError("XML 解析失败") from exc

        depth = 1
        try:
            for event, element in context:
                if event == "start":
                    depth += 1
                    continue
                depth -= 1
                # 深度回到 1 = 根元素的一个直接子元素结束 = 一条记录
                if depth == 1:
                    yield _record(element)
                    root.clear()
        except ET.ParseError as exc:
            raise ReaderError("XML 解析失败") from exc


def _record(element: ET.Element) -> Row:
    """元素 → 行字典（属性 + 子元素）。"""
    record: Row = {f"@{name}": value for name, value in element.attrib.items()}
    for child in element:
        record[child.tag] = "".join(child.itertext()) if len(child) else child.text
    if not record:
        # 叶子记录（无属性无子元素）：把自身文本作为唯一列，避免产出空行
        record = {"text": element.text}
    return record
