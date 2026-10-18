"""M-C 数据连接 — S3 / MinIO 探针（ListObjectsV2，max-keys=1）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-C / FR-05
覆盖 FR：FR-05（安全连通性测试）、FR-13（文件资产来源）
对应 AC：AC-02（失败摘要脱敏：只含端点与状态码）

为什么用 ListObjectsV2 而不是 HEAD bucket：后者需要 `s3:ListBucket` 之外的权限，
且无法区分「bucket 不存在」与「无列举权限」；列举一次（max-keys=1）同时验证
**端点可达 + 凭据有效 + bucket 可读**三件事，且不搬运对象正文。
"""
from __future__ import annotations

import httpx

from etl_agent.connections.probes.base import ProbeConfig, ProbeError
from etl_agent.connections.s3 import S3ListError, list_objects_v2, parse_endpoint


async def probe_s3(config: ProbeConfig) -> None:
    """ListObjectsV2（max-keys=1）实探（FR-05）。

    Raises:
        ProbeError: 定位参数缺失 / 端点不可达 / 凭据无效（消息已脱敏）。
    """
    try:
        endpoint = parse_endpoint(config.host, config.port, config.database_name)
    except ValueError as exc:
        raise ProbeError(str(exc)) from exc

    try:
        await list_objects_v2(
            endpoint,
            (config.username, config.password),
            max_keys=1,
        )
    except S3ListError as exc:
        raise ProbeError(str(exc)) from exc
    except (httpx.HTTPError, OSError) as exc:
        raise ProbeError(
            f"s3 connect failed at {endpoint.host}:{endpoint.port}"
        ) from exc
