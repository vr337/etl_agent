"""M-C 数据连接 — 连通性测试编排（API-03，后台执行 + 脱敏回写）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 6.1 章 API-03 / 第 3.2 章 M-C
覆盖 FR：FR-05（后台测试并回写状态 UNTESTED/TESTING/READY/FAILED 与脱敏错误摘要）
对应 AC：AC-02（失败错误信息绝不含密码）
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.config import Settings
from etl_agent.connections.probes import PROBE_SUPPORTED_TYPES, ProbeConfig, probe_for
from etl_agent.connections.probes.base import ProbeError
from etl_agent.connections.service import load_connection
from etl_agent.infrastructure.models import DataConnection
from etl_agent.secrets.factory import create_provider

logger = logging.getLogger("etl_agent.connections.probe")

# 探针连接超时（秒）：与各探针内部 timeout 一致的兜底值
PROBE_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class ProbeOutcome:
    """探针结果：状态 + 脱敏错误摘要（None 表示成功）。"""

    status: str
    error_summary: str | None = None


def mask_error(message: str, secrets: list[str]) -> str:
    """脱敏：把任何出现在错误信息中的秘密值替换为 ***（AC-02 兜底）。

    驱动异常通常不回传密码，但认证失败路径的报文格式随版本变化——
    宁可误伤也不泄露（PRD 10.5：全链路敏感明文泄露 = 0）。
    """
    masked = message
    for secret in secrets:
        if secret and secret in masked:
            masked = masked.replace(secret, "***")
    return masked[:500]


async def run_probe(
    session: AsyncSession, project_id: uuid.UUID, connection_id: uuid.UUID, settings: Settings
) -> ProbeOutcome:
    """执行连通性测试并回写状态（FR-05）。

    凭据从 SecretProvider 解密后仅在内存中短暂持有（禁止事项 16），
    探针结束即超出作用域；错误摘要经 mask_error 脱敏后才落状态/日志。

    Args:
        session: 请求级数据库会话（BackgroundTasks 在依赖 ExitStack 内执行，会话仍打开）。
        project_id: 项目隔离边界（AC-12：跨项目视同不存在）。
        connection_id: 目标连接。
        settings: 应用配置（SecretProvider 选择）。

    Returns:
        ProbeOutcome: READY（成功）或 FAILED（含脱敏摘要）。
    """
    connection = await load_connection(session, project_id, connection_id)
    provider = create_provider(session, settings)
    secret = await provider.read(connection.secret_ref) if connection.secret_ref else {}
    config = ProbeConfig(
        connector_type=connection.connector_type,
        host=connection.host or "",
        port=connection.port,
        database_name=connection.database_name,
        username=secret.get("username", ""),
        password=secret.get("password", ""),
    )
    try:
        await probe_for(config.connector_type)(config)
    except ProbeError as exc:
        summary = mask_error(str(exc), [config.password, config.username])
        connection.status = "FAILED"
        await session.flush()
        # 日志同样脱敏（S3 提示词 9.13：日志不含 password）
        logger.warning("probe failed: connection=%s summary=%s", connection.id, summary)
        return ProbeOutcome(status="FAILED", error_summary=summary)
    connection.status = "READY"
    await session.flush()
    return ProbeOutcome(status="READY")


def assert_probe_supported(connection: DataConnection) -> None:
    """探针支持性前置校验（fail fast：暂不支持类型在受理阶段即明确拒绝）。

    Raises:
        ConnectorUnsupported: Oracle/SQLServer/Doris/Hive/HDFS（S3 禁止事项 15）。
    """
    from etl_agent.connections.errors import ConnectorUnsupported

    if connection.connector_type not in PROBE_SUPPORTED_TYPES:
        raise ConnectorUnsupported(
            f"connector type not yet supported for probing: {connection.connector_type}"
        )


def testing_started_at() -> datetime:
    """受理时间戳（API-03 响应）。"""
    return datetime.now(UTC)
