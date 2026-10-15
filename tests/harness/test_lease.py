"""执行租约测试（T-12.13；S6 提示词第十二节 tests/harness）。

覆盖（FR-37）：同一准备单的同类型副作用并发 -> 只有一个租约；过期后可再获取。
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from etl_agent.harness.errors import LeaseHeld
from etl_agent.harness.lease import LeaseManager

NOW = __import__("datetime").datetime(2026, 10, 14, tzinfo=__import__("datetime").UTC)


async def test_lease_mutex() -> None:
    """同一租约键并发获取 → 第二个 HARNESS_409_LEASE_HELD（FR-37）。"""
    manager = LeaseManager()
    key = manager.build_key(uuid.uuid4(), "ROLLBACK_DORIS")

    first = await manager.acquire(key, owner="worker-1", now=NOW)
    assert first.owner == "worker-1"

    with pytest.raises(LeaseHeld) as excinfo:
        await manager.acquire(key, owner="worker-2", now=NOW)
    assert excinfo.value.code == "HARNESS_409_LEASE_HELD"

    # 持有者可重入（同 owner），释放后他人可获取
    again = await manager.acquire(key, owner="worker-1", now=NOW)
    assert again.owner == "worker-1"
    await manager.release(key, owner="worker-1")
    third = await manager.acquire(key, owner="worker-2", now=NOW)
    assert third.owner == "worker-2"


async def test_lease_expiry_allows_reacquire() -> None:
    """租约过期后可被重新获取（S7 Worker 失活不永久阻塞，FR-37）。"""
    manager = LeaseManager()
    key = manager.build_key(uuid.uuid4(), "CANCEL_EXECUTION")
    await manager.acquire(key, owner="worker-1", now=NOW, ttl_seconds=60)

    assert await manager.get(key, now=NOW) is not None
    assert await manager.get(key, now=NOW + timedelta(seconds=61)) is None

    later = await manager.acquire(key, owner="worker-2", now=NOW + timedelta(seconds=61))
    assert later.owner == "worker-2"


async def test_lease_key_isolated_by_preparation_and_type() -> None:
    """租约键按 (preparation_id, side_effect_type) 隔离：不同类型可并行。"""
    manager = LeaseManager()
    preparation_id = uuid.uuid4()
    rollback_key = manager.build_key(preparation_id, "ROLLBACK_DORIS")
    cancel_key = manager.build_key(preparation_id, "CANCEL_EXECUTION")
    assert rollback_key != cancel_key

    await manager.acquire(rollback_key, owner="worker-1", now=NOW)
    cancel = await manager.acquire(cancel_key, owner="worker-2", now=NOW)
    assert cancel.owner == "worker-2"
