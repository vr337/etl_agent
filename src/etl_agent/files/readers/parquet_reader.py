"""M-E 文件资产 — Parquet / ORC 适配器。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-E
覆盖 FR：FR-13（Parquet 为需求文档点名格式；ORC 为扩展格式）
对应 AC：AC-02（只产出内存行，脱敏由 masking 统一负责）

**内存边界**：Parquet 走 `ParquetFile.iter_batches` 真流式（与文件大小无关）；
ORC 的 pyarrow 实现只提供整份 `read()`（无 `iter_batches`），故内存上界 =
文件大小上界（25 MiB，FR-13）。两者产出的行数上限仍由调用方统一卡在 100,000。
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from etl_agent.files.readers.base import ReaderError, Row

# 批次大小：兼顾流式内存占用与每批的 Python 转换开销
BATCH_SIZE = 1024


class ParquetReader:
    """Parquet 适配器（按批次惰性读取）。"""

    def iter_rows(self, path: Path) -> Iterator[Row]:
        """按批次迭代 Parquet（FR-13）。

        Raises:
            ReaderError: 文件不是合法 Parquet。
        """
        from pyarrow import parquet

        try:
            parquet_file = parquet.ParquetFile(path)
        except Exception as exc:
            raise ReaderError("Parquet 文件无法解析") from exc
        try:
            for batch in parquet_file.iter_batches(batch_size=BATCH_SIZE):
                yield from batch.to_pylist()
        except Exception as exc:
            raise ReaderError("Parquet 数据页读取失败") from exc


class OrcReader:
    """ORC 适配器（扩展格式，整份读取后逐行产出）。"""

    def iter_rows(self, path: Path) -> Iterator[Row]:
        """迭代 ORC（FR-13 扩展格式）。

        Raises:
            ReaderError: 文件不是合法 ORC。
        """
        from pyarrow import orc

        try:
            table = orc.ORCFile(path).read()
        except Exception as exc:
            raise ReaderError("ORC 文件无法解析") from exc
        yield from table.to_pylist()
