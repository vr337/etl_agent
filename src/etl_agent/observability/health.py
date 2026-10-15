"""M-O 可观测性 — `/health` 扩展（Worker 健康摘要 + 脱敏配置状态，FR-59/38）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-O / 第 5.2 章 worker_heartbeats / 第 8 章「可观测」
覆盖 FR：FR-59（`/health` 返回存活状态与脱敏的 SecretProvider 配置状态，不主动读取凭据）、
        FR-38（Worker 心跳 10s 上报 / 35s 判失活）
对应 AC：AC-11（健康检查可用于存活探针）、AC-02（健康响应零明文、零凭据读取）

三条口径：

1. **脱敏**：只回「是否已配置」（布尔）与心跳聚合事实（时间、数量），
   绝不回密钥、地址口令、连接串；
2. **不主动读凭据**：本模块不实例化 SecretProvider、不调 `read()`——探针接口必须
   零副作用，否则健康检查会变成凭据解密放大器（每次探针解密一次密钥材料）；
3. **判活口径与心跳一致**：`now - last_heartbeat_at > worker_inactive_seconds`（默认 35s）
   即失活（SPEC 8 高可用行），与 Worker 上报间隔 10s 保持 ≥3 倍余量，抗单次丢包。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.config import Settings
from etl_agent.config import settings as default_settings
from etl_agent.infrastructure.models import WorkerHeartbeat
from etl_agent.observability.otel import otel_state

STATUS_OK = "ok"


def secret_provider_summary(settings: Settings) -> dict[str, Any]:
    """SecretProvider 配置状态（只回「是否已配置」，绝不回密钥材料，FR-59）。"""
    if settings.secret_provider == "fernet-db":
        configured = bool(settings.fernet_key.strip())
    elif settings.secret_provider == "vault-kv-v2":
        configured = bool(settings.vault_addr.strip())
    else:
        configured = False
    return {
        "provider": settings.secret_provider,
        "configured": configured,
        "key_version": settings.fernet_key_version
        if settings.secret_provider == "fernet-db"
        else None,
    }


async def worker_summary(
    session: AsyncSession, settings: Settings | None = None, *, now: datetime | None = None
) -> dict[str, Any]:
    """Worker 健康摘要（活跃数 / 总数 / 最近心跳时间；不含 worker_id 明细以外的敏感信息）。

    活跃判定与失活阈值一致：`now - last_heartbeat_at <= worker_inactive_seconds`。
    """
    effective = settings or default_settings
    moment = now or datetime.now(UTC)
    threshold = moment - timedelta(seconds=effective.worker_inactive_seconds)
    total = int(
        (await session.execute(select(func.count()).select_from(WorkerHeartbeat))).scalar() or 0
    )
    active = int(
        (
            await session.execute(
                select(func.count())
                .select_from(WorkerHeartbeat)
                .where(WorkerHeartbeat.last_heartbeat_at >= threshold)
            )
        ).scalar()
        or 0
    )
    latest = (
        await session.execute(select(func.max(WorkerHeartbeat.last_heartbeat_at)))
    ).scalar()
    return {
        "active": active,
        "total": total,
        "inactive_after_seconds": effective.worker_inactive_seconds,
        "latest_heartbeat_at": latest.isoformat() if latest is not None else None,
    }


async def health_payload(
    session: AsyncSession | None, settings: Settings | None = None
) -> dict[str, Any]:
    """`/health` 响应体（存活 + 脱敏配置状态 + Worker 摘要 + 可观测装配态）。

    Args:
        session: 业务库会话；None 时跳过 Worker 摘要（探针在库不可用时应仍能回存活）。
        settings: 应用配置；缺省全局配置。

    Returns:
        dict: 不含任何凭据、连接串或内部栈。
    """
    effective = settings or default_settings
    payload: dict[str, Any] = {
        "status": STATUS_OK,
        "secret_provider": secret_provider_summary(effective),
        "observability": {
            "otel_enabled": effective.otel_enabled,
            "otel_components": list(otel_state().components),
        },
    }
    if session is not None:
        payload["workers"] = await worker_summary(session, effective)
    return payload


__all__ = [
    "STATUS_OK",
    "health_payload",
    "secret_provider_summary",
    "worker_summary",
]
