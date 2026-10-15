"""M-J Harness 安全执行内核 — 包入口。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 1.3 章设计原则（Harness 是唯一被授权触发副作用的模块）
覆盖 FR：FR-29~37（策略内核、执行协议、Tool Broker、事务 Outbox、副作用同协议）
对应 AC：AC-01（四眼职责分离）、AC-05（prepare→approve→commit + 单次令牌 + 防重放）

模块边界（SPEC 3.2 边界说明）：其余模块只能通过 Harness 发起执行 / 回滚 / 凭据操作；
Harness 不直连数据源、不自行判定副作用（以注册清单为准）、不存原始数据与 Prompt。
"""
