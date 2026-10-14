"""M-C 数据连接 — 本地文件探针（路径存在性 + 可读性）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / FR-05
覆盖 FR：FR-05（安全连通性测试）
对应 AC：AC-02（失败信息只含路径，不含任何凭据）
"""
from __future__ import annotations

import os
from pathlib import Path

from etl_agent.connections.probes.base import ProbeConfig, ProbeError


async def probe_local_file(config: ProbeConfig) -> None:
    """LOCAL_FILE 探针：host 承载文件路径（FR-04/05）。

    Raises:
        ProbeError: 路径不存在或不可读（消息只含路径）。
    """
    if not config.host:
        raise ProbeError("LOCAL_FILE 探针需要 host（文件路径）")
    path = Path(config.host)
    if not path.exists():
        raise ProbeError(f"local file not found: {config.host}")
    if not os.access(path, os.R_OK):
        raise ProbeError(f"local file not readable: {config.host}")
