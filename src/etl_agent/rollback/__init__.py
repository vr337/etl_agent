"""M-L 受管回滚 — 包入口（FR-48，AC-08 核心）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 5.2 章 rollback_requests /
rollback_cleanup_requests / 第 6.1 章 API-13 / 第 8 章「安全 · 授权与职责分离」
覆盖 FR：FR-48（受管回滚 + 制品保留期 7 天 + 容量预警阈值 20 + 到期清理）
对应 AC：AC-08（受管回滚走 Harness 协议：审批 → Commit → Outbox → Worker → 恢复原状态）

模块边界（SPEC 3.2 M-L）：

- **回滚不绕过 Harness**：本包在 S6 的 PDP / Capability / Outbox 之上编排，
  不提供「跳过审批直接执行」的入口（禁止事项 17）；
- **只碰影子表 / 错误表**：数据面动作为 `DROP_SHADOW_TABLE`，永不反向 Swap 正式表；
- **状态单向前进**：回滚申请与制品各有状态机，非法迁移一律拒绝。

装配顺序：`artifacts`（制品清单 / 5 态）→ `retention`（保留期 / 容量预警）
→ `service`（回滚申请状态机）→ `cleanup`（到期清理申请）→ `side_effects`（Harness 协议副作用）
→ `router` / `schemas`（API-13）。
"""

from __future__ import annotations

__all__: list[str] = []
