"""M-K/T-14 调度 — 包入口。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-K / 第 5.2 章 pipeline_schedules / 第 8 章「高可用 · 调度扫描 30s」
覆盖 FR：FR-40（cron + 时区 + BATCH / WATERMARK_INCREMENTAL + misfire SKIP/FIRE_ONCE 宽限 120s）
         FR-41（水印推进与 `watermark_window`）
对应 AC：AC-06（调度到期触发经 Harness 准备单通道，不绕过授权）

模块边界：本包只做「调度契约与到期判定」——不执行副作用、不签发令牌。
到期触发的副作用出口是 Harness（Prepare → 四眼审批 → Commit → Outbox → Worker），
调度扫描只是「到期即创建受管请求」的时钟（FR-34：副作用唯一出口）。
"""
