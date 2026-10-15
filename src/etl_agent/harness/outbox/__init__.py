"""M-J Harness 安全执行内核 — 事务 Outbox。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 5.2 章 harness_command_outbox / 第 8 章「高可用 · Outbox」
覆盖 FR：FR-35（事务 Outbox 可靠投递）
对应 AC：AC-05（Commit 同事务写 Outbox）、AC-06/08 前置
"""
