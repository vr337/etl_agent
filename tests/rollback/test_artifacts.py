"""回滚制品清单与 5 态用例（T-16.16，FR-48）。

覆盖：5 个制品状态齐备、迁移白名单逐条可达、清单构建与校验、
缺必填字段 → INVALID_METADATA、清单只含引用不含数据（AC-02）。
"""

from __future__ import annotations

import uuid

import pytest

from etl_agent.rollback.artifacts import (
    ALL_ARTIFACT_STATUSES,
    ARTIFACT_TRANSITIONS,
    KIND_ERROR_TABLE,
    KIND_SHADOW_TABLE,
    REQUIRED_MANIFEST_KEYS,
    STATUS_AVAILABLE,
    STATUS_CLEANUP_PENDING,
    STATUS_INVALID_METADATA,
    STATUS_ROLLBACK_PENDING,
    STATUS_ROLLED_BACK,
    TERMINAL_ARTIFACT_STATUSES,
    build_manifest,
    can_transition,
    manifest_shadow_table,
    transition_status,
    validate_manifest,
)
from etl_agent.rollback.errors import RollbackStateConflict

RUN_ID = uuid.uuid4()


def test_artifact_5_states() -> None:
    """5 个制品状态齐备（FR-48）。"""
    assert set(ALL_ARTIFACT_STATUSES) == {
        "AVAILABLE",
        "ROLLED_BACK",
        "ROLLBACK_PENDING",
        "CLEANUP_PENDING",
        "INVALID_METADATA",
    }
    assert TERMINAL_ARTIFACT_STATUSES == {STATUS_INVALID_METADATA}


def test_artifact_transitions_all() -> None:
    """制品迁移白名单逐条可达；终态不可再迁移。"""
    assert can_transition(STATUS_AVAILABLE, STATUS_ROLLBACK_PENDING)
    assert can_transition(STATUS_ROLLBACK_PENDING, STATUS_ROLLED_BACK)
    assert can_transition(STATUS_ROLLBACK_PENDING, STATUS_AVAILABLE)
    assert can_transition(STATUS_ROLLED_BACK, STATUS_CLEANUP_PENDING)
    assert can_transition(STATUS_CLEANUP_PENDING, STATUS_INVALID_METADATA)
    assert ARTIFACT_TRANSITIONS[STATUS_INVALID_METADATA] == frozenset()


def test_illegal_artifact_transition_rejected() -> None:
    """AVAILABLE 不可直接跳到 ROLLED_BACK（跳过申请边）。"""
    with pytest.raises(RollbackStateConflict):
        transition_status(STATUS_AVAILABLE, STATUS_ROLLED_BACK)


def test_build_manifest_shape() -> None:
    """清单只含表名与引用（`doris://`），不含数据与凭据（AC-02）。"""
    manifest = build_manifest(
        run_id=RUN_ID,
        target_table="orders_wide",
        shadow_table="orders_wide_shadow",
        error_table="orders_wide_error",
        retention_days=7,
        capacity_warn_threshold=20,
    )
    assert manifest["run_id"] == str(RUN_ID)
    assert manifest["target_table"] == "orders_wide"
    assert manifest["retention_days"] == 7
    kinds = {entry["kind"] for entry in manifest["entries"]}
    assert kinds == {KIND_SHADOW_TABLE, KIND_ERROR_TABLE}
    for entry in manifest["entries"]:
        assert entry["uri"].startswith("doris://")
    # 无 connection string / password 类字段
    text = str(manifest).lower()
    assert "password" not in text and "jdbc://" not in text


def test_build_manifest_without_error_table() -> None:
    """无错误表时只落影子表一个条目。"""
    manifest = build_manifest(
        run_id=RUN_ID,
        target_table="t",
        shadow_table="t_shadow",
        error_table=None,
        retention_days=7,
        capacity_warn_threshold=20,
    )
    assert len(manifest["entries"]) == 1
    assert manifest["entries"][0]["kind"] == KIND_SHADOW_TABLE


def test_validate_manifest_reports_missing_keys() -> None:
    """缺必填字段 → invalid 且 problems 逐项列出。"""
    result = validate_manifest(
        {"entries": [{"name": "x", "kind": "SHADOW_TABLE", "uri": "doris://x"}]}
    )
    assert result.valid is False
    for key in REQUIRED_MANIFEST_KEYS:
        assert any(key in problem for problem in result.problems)


def test_validate_manifest_rejects_empty_entries() -> None:
    """清单无条目 → invalid（至少应有一个制品条目）。"""
    valid, _ = _valid_payload()
    manifest = dict(valid)
    manifest["entries"] = []
    result = validate_manifest(manifest)
    assert result.valid is False


def test_validate_manifest_accepts_complete() -> None:
    """完整清单 → valid。"""
    manifest, _ = _valid_payload()
    result = validate_manifest(manifest)
    assert result.valid is True
    assert result.summary() == "元数据有效"


def test_validate_manifest_rejects_non_dict() -> None:
    """非 JSON 对象 → invalid。"""
    assert validate_manifest("not-a-dict").valid is False


def test_manifest_shadow_table_extracts() -> None:
    """从清单取影子表名（回滚清理目标）。"""
    manifest, _ = _valid_payload(shadow="orders_wide_shadow")
    assert manifest_shadow_table(manifest) == "orders_wide_shadow"
    assert manifest_shadow_table({}) is None
    assert manifest_shadow_table(None) is None


def _valid_payload(*, shadow: str = "t_shadow") -> tuple[dict, dict]:
    manifest = build_manifest(
        run_id=RUN_ID,
        target_table="t",
        shadow_table=shadow,
        error_table=None,
        retention_days=7,
        capacity_warn_threshold=20,
    )
    return manifest, {"shadow": shadow}
