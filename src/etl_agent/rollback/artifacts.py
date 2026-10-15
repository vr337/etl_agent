"""M-L 受管回滚 — 回滚制品清单与 5 态（FR-48）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 5.2 章 rollback_requests
（`artifact_manifest` / `artifact_status`）
覆盖 FR：FR-48（回滚制品清单 AVAILABLE/ROLLED_BACK/ROLLBACK_PENDING/CLEANUP_PENDING/INVALID_METADATA）
对应 AC：AC-08（回滚制品可追溯）、AC-02（清单只含引用与摘要，不含数据）

**清单里装什么**（`rollback_requests.artifact_manifest` JSON 列）：

```
{
  "run_id": "...", "target_table": "orders_wide", "shadow_table": "orders_wide_shadow",
  "error_table": "orders_wide_error", "retention_days": 7,
  "capacity_warn_threshold": 20, "entries": [{"name": "...", "kind": "SHADOW_TABLE", "uri": "..."}],
  "created_at": "..."
}
```

只装**表名与引用**，不装数据、不装连接串、不装凭据（AC-02）。
`INVALID_METADATA` 是「清单本身不可用」的状态（缺必填字段 / 摘要形态非法），
不是「数据不可用」——两者处置不同：前者修元数据，后者走重跑。

状态机（单向，除 AVAILABLE ↔ ROLLBACK_PENDING 的申请/回退边）：

```
AVAILABLE ──(申请回滚)──► ROLLBACK_PENDING ──(执行成功)──► ROLLED_BACK ──(到期清理)──► CLEANUP_PENDING ──► （清理完成，行保留）
     ▲                            │
     └──(执行失败，回到可回滚)─────┘
任意状态 ──(元数据校验失败)──► INVALID_METADATA（终态，需人工修数据）
```
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from etl_agent.rollback.errors import RollbackStateConflict

STATUS_AVAILABLE = "AVAILABLE"
STATUS_ROLLED_BACK = "ROLLED_BACK"
STATUS_ROLLBACK_PENDING = "ROLLBACK_PENDING"
STATUS_CLEANUP_PENDING = "CLEANUP_PENDING"
STATUS_INVALID_METADATA = "INVALID_METADATA"

ALL_ARTIFACT_STATUSES: tuple[str, ...] = (
    STATUS_AVAILABLE,
    STATUS_ROLLED_BACK,
    STATUS_ROLLBACK_PENDING,
    STATUS_CLEANUP_PENDING,
    STATUS_INVALID_METADATA,
)

ARTIFACT_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_AVAILABLE: frozenset({STATUS_ROLLBACK_PENDING, STATUS_INVALID_METADATA}),
    STATUS_ROLLBACK_PENDING: frozenset(
        {STATUS_ROLLED_BACK, STATUS_AVAILABLE, STATUS_INVALID_METADATA}
    ),
    STATUS_ROLLED_BACK: frozenset({STATUS_CLEANUP_PENDING, STATUS_INVALID_METADATA}),
    STATUS_CLEANUP_PENDING: frozenset({STATUS_INVALID_METADATA}),
    STATUS_INVALID_METADATA: frozenset(),
}

TERMINAL_ARTIFACT_STATUSES: frozenset[str] = frozenset({STATUS_INVALID_METADATA})

# 制品种类（清单条目）
KIND_SHADOW_TABLE = "SHADOW_TABLE"
KIND_ERROR_TABLE = "ERROR_TABLE"

# 清单必填字段（缺失即 INVALID_METADATA）
REQUIRED_MANIFEST_KEYS: tuple[str, ...] = ("run_id", "target_table", "shadow_table")


@dataclass(frozen=True)
class ArtifactEntry:
    """制品条目（只含引用与摘要，不含数据）。"""

    name: str
    kind: str
    uri: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "kind": self.kind, "uri": self.uri}


@dataclass(frozen=True)
class ArtifactValidation:
    """清单校验结果（invalid 时 problems 给出缺项，供诊断直接引用）。"""

    valid: bool
    problems: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        return "元数据有效" if self.valid else "；".join(self.problems)


def can_transition(current: str, target: str) -> bool:
    """制品状态迁移白名单判定。"""
    return target in ARTIFACT_TRANSITIONS.get(current, frozenset())


def transition_status(current: str, target: str) -> str:
    """推进制品状态（非法迁移抛状态冲突）。

    Raises:
        RollbackStateConflict: 非法迁移（终态再迁移 / 跳跃迁移）。
    """
    if not can_transition(current, target):
        raise RollbackStateConflict(
            f"回滚制品状态 {current} 不可迁移到 {target}（FR-48 制品状态机）"
        )
    return target


def build_manifest(
    *,
    run_id: uuid.UUID,
    target_table: str,
    shadow_table: str,
    error_table: str | None,
    retention_days: int,
    capacity_warn_threshold: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """构造回滚制品清单（表名 + 保留期事实，不含数据与凭据）。"""
    entries = [
        ArtifactEntry(
            name=shadow_table, kind=KIND_SHADOW_TABLE, uri=f"doris://{shadow_table}"
        ).as_dict()
    ]
    if error_table:
        entries.append(
            ArtifactEntry(
                name=error_table, kind=KIND_ERROR_TABLE, uri=f"doris://{error_table}"
            ).as_dict()
        )
    return {
        "run_id": str(run_id),
        "target_table": target_table,
        "shadow_table": shadow_table,
        "error_table": error_table,
        "retention_days": retention_days,
        "capacity_warn_threshold": capacity_warn_threshold,
        "entries": entries,
        "created_at": (now or datetime.now(UTC)).isoformat(),
    }


def validate_manifest(manifest: object) -> ArtifactValidation:
    """校验清单元数据完整性（缺必填字段 → INVALID_METADATA）。"""
    if not isinstance(manifest, dict):
        return ArtifactValidation(valid=False, problems=("清单不是 JSON 对象",))
    problems: list[str] = []
    for key in REQUIRED_MANIFEST_KEYS:
        value = manifest.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            problems.append(f"缺少字段 {key}")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        problems.append("清单为空（至少应有一个制品条目）")
    else:
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or not entry.get("name") or not entry.get("kind"):
                problems.append(f"第 {index + 1} 个条目缺少 name / kind")
    return ArtifactValidation(valid=not problems, problems=tuple(problems))


def manifest_shadow_table(manifest: object) -> str | None:
    """从清单取影子表名（回滚动作的清理目标）。"""
    if isinstance(manifest, dict):
        value = manifest.get("shadow_table")
        if isinstance(value, str) and value:
            return value
    return None


__all__ = [
    "ALL_ARTIFACT_STATUSES",
    "ARTIFACT_TRANSITIONS",
    "KIND_ERROR_TABLE",
    "KIND_SHADOW_TABLE",
    "REQUIRED_MANIFEST_KEYS",
    "STATUS_AVAILABLE",
    "STATUS_CLEANUP_PENDING",
    "STATUS_INVALID_METADATA",
    "STATUS_ROLLBACK_PENDING",
    "STATUS_ROLLED_BACK",
    "TERMINAL_ARTIFACT_STATUSES",
    "ArtifactEntry",
    "ArtifactValidation",
    "build_manifest",
    "can_transition",
    "manifest_shadow_table",
    "transition_status",
    "validate_manifest",
]
