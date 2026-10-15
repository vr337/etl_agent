"""M-J/M-K 执行 Worker — Celery 任务包（Beat 四类周期任务的实现体）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 4.3 章进程与入口 / 第 8 章「高可用 · Worker 可靠性」
覆盖 FR：FR-35（Outbox 扫描）、FR-38（心跳）、FR-10（凭据扫描）、FR-40（调度扫描）
对应 AC：AC-06（Worker 调度 SeaTunnel + 影子表 + 质量分流）

任务薄壳约定：Celery 任务函数只做「开会话 + 调服务 + 提交」，
可测逻辑全部在接收 session 的服务函数里（测试用夹具会话直接驱动，
不必启动 Celery Broker——真实验收由 DoD-3/8 的 pytest 覆盖）。
"""
