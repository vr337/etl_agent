"""M-J Harness 安全执行内核 — Tool Broker 与 Replay Guard。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 8 章「高可用 · Outbox」与「安全 · 防重放与授权」
覆盖 FR：FR-34（唯一副作用出口 + 防重放 Guard）、FR-35（事务 Outbox 写入）
对应 AC：AC-05（唯一出口 + 单次消费）
"""
