"""M-L 运行时监督与受管回滚 — 包入口。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 5.2 章 runtime_supervision_snapshots /
第 8 章「性能 · 运行时预算硬上限」
覆盖 FR：FR-45（冻结预算 + 6 条确定性规则 + 快照）、FR-46（减损动作 + 隔离 + 外部动作独立重试）
对应 AC：AC-08（超预算 → 硬中断 + 隔离 + 根因诊断）

模块边界（SPEC 3.2 M-L）：
- **只做监督判决与减损动作**，不改作业语义（禁止事项 14：只能收窄权限，不能扩权）；
- 快照只装规则证据与聚合指标，不含日志正文与原始数据；
- 外部减损动作失败**不阻塞**监督快照写入（禁止事项 18）；
- 隔离是单向事实，解除需人工裁决。

装配顺序：`contracts`（契约 + 规则注册表）→ `rules/*`（6 条规则）→ `mitigation`（动作聚合）
→ `snapshot`（快照 + 摘要）→ `interrupt` / `quarantine` / `external_mitigation`（动作执行）。
"""
