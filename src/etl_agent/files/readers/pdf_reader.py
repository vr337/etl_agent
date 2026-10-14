"""M-E 文件资产 — PDF 适配器。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-E
覆盖 FR：FR-13（PDF 为扩展格式）
对应 AC：AC-02（只产出内存行，脱敏由 masking 统一负责）

**抽取限制（S4 提示词十六节）**：
- 只做**文本层**抽取（pdfplumber），不引入 OCR —— 扫描件 / 图片型 PDF 的
  `text` 为空字符串，属预期行为而非错误（S4 不承担 OCR 依赖与算力）；
- 单页抽取失败不中断整份探查（该页 `text` 置空），避免一页异常毁掉整个 Profile；
- 行 = 页，行数上限由调用方统一卡在 100,000（页数远超该值即触发 422）。
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from etl_agent.files.readers.base import ReaderError, Row


class PdfReader:
    """PDF 适配器（一页一行：page + text）。"""

    def iter_rows(self, path: Path) -> Iterator[Row]:
        """按页迭代 PDF 文本层（FR-13 扩展格式）。

        Raises:
            ReaderError: PDF 无法打开（损坏 / 加密 / 非 PDF）。
        """
        import pdfplumber

        try:
            pdf = pdfplumber.open(str(path))
        except Exception as exc:
            raise ReaderError("PDF 文件无法解析") from exc
        try:
            for number, page in enumerate(pdf.pages, start=1):
                try:
                    text = page.extract_text() or ""
                except Exception:  # noqa: BLE001 —— 单页失败降级为空文本
                    text = ""
                yield {"page": number, "text": text}
        finally:
            pdf.close()
