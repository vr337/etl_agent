"""M-K/T-14 调度 — API 出入参模型（调度策略 CRUD）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-K / 第 5.2 章 pipeline_schedules / 第 6.4 章错误码
覆盖 FR：FR-40（cron + 时区 + 同步模式 + misfire 策略）、FR-41（水印增量契约）
对应 AC：AC-06（调度策略可治理可观测）

口径：调度策略 CRUD 不在 SPEC 6.1 的核心 15 端点内（97 端点全集的常规资源管理面），
出入参**严格对齐 SPEC 5.2 `pipeline_schedules` 的既有列**，不自造字段。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from etl_agent.scheduling.service import DEFAULT_TIMEZONE, MODE_BATCH


class ScheduleUpsertRequest(BaseModel):
    """创建 / 更新调度策略（同管线一策略，幂等覆盖）。"""

    cron: str = Field(description="5 段 cron（分 时 日 月 星期）")
    timezone: str = Field(default=DEFAULT_TIMEZONE, description="IANA 时区名")
    mode: str = Field(default=MODE_BATCH, description="BATCH / WATERMARK_INCREMENTAL")
    misfire_policy: str = Field(default="SKIP", description="SKIP / FIRE_ONCE")
    misfire_grace_seconds: int = Field(default=120, gt=0, description="misfire 宽限（秒）")
    watermark_column: str | None = Field(default=None, description="增量水印列（TIMESTAMP）")
    watermark_initial: str | None = Field(default=None, description="水印初始值")
    watermark_lag_seconds: int | None = Field(default=None, ge=0, description="水印滞后秒数")


class ScheduleResponse(BaseModel):
    """调度策略视图（只回契约字段，不含任何执行事实）。"""

    id: uuid.UUID
    pipeline_id: uuid.UUID
    cron: str
    timezone: str
    mode: str
    misfire_policy: str
    misfire_grace_seconds: int
    watermark_column: str | None = None
    watermark_initial: str | None = None
    watermark_lag_seconds: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


__all__ = ["ScheduleResponse", "ScheduleUpsertRequest"]
