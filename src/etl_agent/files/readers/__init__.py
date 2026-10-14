"""M-E 文件资产 — 格式适配器注册表与格式识别。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-E / 第 5.2 章 file_assets.format
覆盖 FR：FR-13（9 种格式：CSV / JSON / JSONL / XLSX / PARQUET + 扩展 ORC / XML / MARKDOWN / PDF）、FR-64（点名格式覆盖核对）
对应 AC：AC-02（适配器只产出内存行，脱敏由 profiles.masking 统一负责）

格式识别为**双通道**（S4 提示词 9.3）：二进制魔数嗅探优先（内容权威），
其次回落到扩展名；两通道都认不出即拒绝（422 FILE_FORMAT_UNSUPPORTED），
不做「猜一个格式硬读」的降级 —— 错读会把格式错误伪装成数据错误。
"""
from __future__ import annotations

from etl_agent.files.readers.base import (
    ColumnSpec,
    FileReader,
    ReaderError,
    Row,
    UnsupportedFormat,
    infer_columns,
    infer_type,
)
from etl_agent.files.readers.csv_reader import CsvReader
from etl_agent.files.readers.excel_reader import ExcelReader
from etl_agent.files.readers.json_reader import JsonLinesReader, JsonReader
from etl_agent.files.readers.markdown_reader import MarkdownReader
from etl_agent.files.readers.parquet_reader import OrcReader, ParquetReader
from etl_agent.files.readers.pdf_reader import PdfReader
from etl_agent.files.readers.xml_reader import XmlReader

__all__ = [
    "SUPPORTED_FORMATS",
    "ColumnSpec",
    "FileReader",
    "ReaderError",
    "Row",
    "UnsupportedFormat",
    "detect_format",
    "infer_columns",
    "infer_type",
    "reader_for",
]

# 9 种受支持格式（FR-13；FR-64 点名 CSV/Excel(XLSX)/JSON/Parquet 四项已覆盖）
SUPPORTED_FORMATS: tuple[str, ...] = (
    "CSV",
    "JSON",
    "JSONL",
    "XLSX",
    "PARQUET",
    "ORC",
    "XML",
    "MARKDOWN",
    "PDF",
)

_EXTENSION_FORMATS: dict[str, str] = {
    ".csv": "CSV",
    ".json": "JSON",
    ".jsonl": "JSONL",
    ".ndjson": "JSONL",
    ".xlsx": "XLSX",
    ".parquet": "PARQUET",
    ".pq": "PARQUET",
    ".orc": "ORC",
    ".xml": "XML",
    ".md": "MARKDOWN",
    ".markdown": "MARKDOWN",
    ".pdf": "PDF",
}

_READERS: dict[str, FileReader] = {
    "CSV": CsvReader(),
    "JSON": JsonReader(),
    "JSONL": JsonLinesReader(),
    "XLSX": ExcelReader(),
    "PARQUET": ParquetReader(),
    "ORC": OrcReader(),
    "XML": XmlReader(),
    "MARKDOWN": MarkdownReader(),
    "PDF": PdfReader(),
}


def detect_format(filename: str | None, head: bytes) -> str:
    """识别文件格式（魔数嗅探优先，扩展名兜底）。

    Args:
        filename: 上传文件名（可为 None —— multipart 不强制带文件名）。
        head: 文件头部若干字节（建议 ≥8 字节）。

    Returns:
        9 种格式之一。

    Raises:
        UnsupportedFormat: 两个通道都识别不出（422 FILE_FORMAT_UNSUPPORTED）。
    """
    sniffed = _sniff(head)
    if sniffed is not None:
        return sniffed
    extension = _extension_of(filename)
    if extension is not None:
        return extension
    raise UnsupportedFormat(
        f"文件格式不受支持（仅支持 {'/'.join(SUPPORTED_FORMATS)}）"
    )


def reader_for(file_format: str) -> FileReader:
    """取格式对应的适配器。

    Raises:
        UnsupportedFormat: 格式不在 9 种之内。
    """
    reader = _READERS.get(file_format)
    if reader is None:
        raise UnsupportedFormat(
            f"文件格式不受支持: {file_format!r}（仅支持 {'/'.join(SUPPORTED_FORMATS)}）"
        )
    return reader


def _sniff(head: bytes) -> str | None:
    """魔数嗅探（仅对二进制/自描述格式生效；纯文本格式交给扩展名判定）。"""
    if head.startswith(b"%PDF-"):
        return "PDF"
    if head.startswith(b"PAR1"):
        return "PARQUET"
    if head.startswith(b"ORC"):
        return "ORC"
    if head.startswith(b"PK\x03\x04"):
        # XLSX 是 zip 容器；docx/pptx 同魔数，但都不在受支持格式内，故直接判定 XLSX
        return "XLSX"
    stripped = head.lstrip()
    if stripped.startswith((b"<?xml", b"<")):
        return "XML"
    return None


def _extension_of(filename: str | None) -> str | None:
    """按扩展名判定格式（大小写无关）。"""
    if not filename:
        return None
    lowered = filename.lower()
    for extension, file_format in _EXTENSION_FORMATS.items():
        if lowered.endswith(extension):
            return file_format
    return None
