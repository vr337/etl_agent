"""调度策略路由用例（T-14.5 / FR-40/41）。

覆盖：策略 CRUD（PUT / GET / LIST / DELETE）、参数非法 400、跨项目隔离、
增量模式必填水印契约、扫描周期 30s 配置。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.config import settings as default_settings
from etl_agent.infrastructure.models import PipelineSchedule
from etl_agent.scheduling.service import due_slot
from etl_agent.worker import app as worker_app
from tests.helpers import SeededWorkspace, make_client

BODY = {
    "cron": "0 2 * * *",
    "timezone": "Asia/Shanghai",
    "mode": "BATCH",
    "misfire_policy": "SKIP",
    "misfire_grace_seconds": 120,
}


async def test_upsert_and_get_schedule(
    app: FastAPI, seeded: SeededWorkspace, seeded_pipeline: uuid.UUID
) -> None:
    """PUT 创建策略后 GET 可取回同一事实（一管线一策略）。"""
    headers = {"X-Actor-ID": SeededWorkspace.ADMIN_A}
    async with make_client(app) as client:
        created = await client.put(
            f"/api/v1/projects/{seeded.project_a_id}/pipelines/{seeded_pipeline}/schedule",
            json=BODY,
            headers=headers,
        )
        fetched = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/pipelines/{seeded_pipeline}/schedule",
            headers=headers,
        )
    assert created.status_code == 200, created.text
    assert created.json()["cron"] == "0 2 * * *"
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created.json()["id"]


async def test_upsert_is_idempotent_overwrite(
    app: FastAPI, seeded: SeededWorkspace, seeded_pipeline: uuid.UUID
) -> None:
    """重复 PUT 覆盖同一行（不产生第二条策略）。"""
    headers = {"X-Actor-ID": SeededWorkspace.ADMIN_A}
    async with make_client(app) as client:
        first = await client.put(
            f"/api/v1/projects/{seeded.project_a_id}/pipelines/{seeded_pipeline}/schedule",
            json=BODY,
            headers=headers,
        )
        second = await client.put(
            f"/api/v1/projects/{seeded.project_a_id}/pipelines/{seeded_pipeline}/schedule",
            json={**BODY, "cron": "*/15 * * * *", "misfire_policy": "FIRE_ONCE"},
            headers=headers,
        )
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["cron"] == "*/15 * * * *"
    assert second.json()["misfire_policy"] == "FIRE_ONCE"


async def test_list_schedules(
    app: FastAPI, seeded: SeededWorkspace, seeded_pipeline: uuid.UUID
) -> None:
    """列出项目内策略（跨项目不出现）。"""
    headers = {"X-Actor-ID": SeededWorkspace.ADMIN_A}
    async with make_client(app) as client:
        await client.put(
            f"/api/v1/projects/{seeded.project_a_id}/pipelines/{seeded_pipeline}/schedule",
            json=BODY,
            headers=headers,
        )
        listed = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/schedules", headers=headers
        )
    assert listed.status_code == 200
    assert [item["pipeline_id"] for item in listed.json()] == [str(seeded_pipeline)]


async def test_delete_schedule(
    app: FastAPI, seeded: SeededWorkspace, seeded_pipeline: uuid.UUID
) -> None:
    """删除策略后 GET 404（幂等语义：不存在即 404）。"""
    headers = {"X-Actor-ID": SeededWorkspace.ADMIN_A}
    async with make_client(app) as client:
        await client.put(
            f"/api/v1/projects/{seeded.project_a_id}/pipelines/{seeded_pipeline}/schedule",
            json=BODY,
            headers=headers,
        )
        deleted = await client.delete(
            f"/api/v1/projects/{seeded.project_a_id}/pipelines/{seeded_pipeline}/schedule",
            headers=headers,
        )
        after = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/pipelines/{seeded_pipeline}/schedule",
            headers=headers,
        )
    assert deleted.status_code == 204
    assert after.status_code == 404


async def test_invalid_cron_rejected(
    app: FastAPI, seeded: SeededWorkspace, seeded_pipeline: uuid.UUID
) -> None:
    """cron 非法 → BIZ_400_VALIDATION 且不落库。"""
    async with make_client(app) as client:
        response = await client.put(
            f"/api/v1/projects/{seeded.project_a_id}/pipelines/{seeded_pipeline}/schedule",
            json={**BODY, "cron": "not a cron"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 400
    assert response.json()["code"] == "BIZ_400_VALIDATION"


async def test_invalid_misfire_policy_rejected(
    app: FastAPI, seeded: SeededWorkspace, seeded_pipeline: uuid.UUID
) -> None:
    """misfire 策略非法 → 400。"""
    async with make_client(app) as client:
        response = await client.put(
            f"/api/v1/projects/{seeded.project_a_id}/pipelines/{seeded_pipeline}/schedule",
            json={**BODY, "misfire_policy": "ALWAYS"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 400


async def test_incremental_requires_watermark_contract(
    app: FastAPI, seeded: SeededWorkspace, seeded_pipeline: uuid.UUID
) -> None:
    """WATERMARK_INCREMENTAL 未声明水印列 → 400（不静默退化为全量）。"""
    async with make_client(app) as client:
        response = await client.put(
            f"/api/v1/projects/{seeded.project_a_id}/pipelines/{seeded_pipeline}/schedule",
            json={**BODY, "mode": "WATERMARK_INCREMENTAL"},
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 400


async def test_incremental_accepts_full_contract(
    app: FastAPI, seeded: SeededWorkspace, seeded_pipeline: uuid.UUID
) -> None:
    """声明完整水印契约即可创建（FR-41）。"""
    async with make_client(app) as client:
        response = await client.put(
            f"/api/v1/projects/{seeded.project_a_id}/pipelines/{seeded_pipeline}/schedule",
            json={
                **BODY,
                "mode": "WATERMARK_INCREMENTAL",
                "watermark_column": "updated_at",
                "watermark_initial": "2026-01-01T00:00:00+00:00",
                "watermark_lag_seconds": 300,
            },
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "WATERMARK_INCREMENTAL"
    assert response.json()["watermark_lag_seconds"] == 300


async def test_cross_project_denied(
    app: FastAPI, seeded: SeededWorkspace, seeded_pipeline_b: uuid.UUID
) -> None:
    """项目 A 成员访问项目 B 管线的调度策略 → 403 / 404（不泄露存在性，AC-12）。"""
    async with make_client(app) as client:
        response = await client.put(
            f"/api/v1/projects/{seeded.project_b_id}/pipelines/{seeded_pipeline_b}/schedule",
            json=BODY,
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 403


async def test_unknown_pipeline_is_404(
    app: FastAPI, seeded: SeededWorkspace
) -> None:
    """不存在的管线 → 404。"""
    async with make_client(app) as client:
        response = await client.get(
            f"/api/v1/projects/{seeded.project_a_id}/pipelines/{uuid.uuid4()}/schedule",
            headers={"X-Actor-ID": SeededWorkspace.ADMIN_A},
        )
    assert response.status_code == 404


async def test_scan_interval_30s(
    db_session: AsyncSession, seeded: SeededWorkspace, seeded_pipeline: uuid.UUID
) -> None:
    """调度扫描周期 30s（FR-40 / SPEC 4.3 Beat 表）。"""
    assert default_settings.scheduler_scan_interval_seconds == 30
    entry = worker_app.conf.beat_schedule["schedule-scan"]
    assert entry["schedule"].run_every.total_seconds() == 30


async def test_due_slot_uses_created_at_anchor(
    db_session: AsyncSession, seeded: SeededWorkspace, seeded_pipeline: uuid.UUID
) -> None:
    """到期槽位以 `created_at` 为锚点（策略创建之前的槽位不属于本次调度事实）。"""
    from datetime import UTC, datetime, timedelta

    schedule = PipelineSchedule(
        pipeline_id=seeded_pipeline,
        cron="*/5 * * * *",
        timezone="Asia/Shanghai",
        mode="BATCH",
        misfire_policy="SKIP",
        misfire_grace_seconds=120,
    )
    db_session.add(schedule)
    await db_session.flush()
    # 策略一小时前创建：最近一个槽位（≤5 分钟前）必然晚于锚点，故应判定为到期
    schedule.created_at = datetime.now(UTC) - timedelta(hours=1)
    await db_session.flush()

    now = datetime.now(UTC)
    slot, decision = await due_slot(schedule, now=now)

    assert slot <= now
    assert slot > schedule.created_at
    assert decision.policy == "SKIP"
    assert decision.reason in {"within-grace", "misfire-skip"}


async def test_due_slot_before_anchor_is_not_due(
    db_session: AsyncSession, seeded: SeededWorkspace, seeded_pipeline: uuid.UUID
) -> None:
    """策略刚创建（锚点 = 现在）→ 尚无到期槽位（不把创建前的历史槽位算成本次调度）。"""
    from datetime import UTC, datetime

    from etl_agent.scheduling.errors import ScheduleNotFound

    schedule = PipelineSchedule(
        pipeline_id=seeded_pipeline,
        cron="0 2 * * *",
        timezone="Asia/Shanghai",
        mode="BATCH",
        misfire_policy="SKIP",
        misfire_grace_seconds=120,
    )
    db_session.add(schedule)
    await db_session.flush()
    schedule.created_at = datetime.now(UTC)
    await db_session.flush()

    with pytest.raises(ScheduleNotFound):
        await due_slot(schedule, now=datetime.now(UTC))
