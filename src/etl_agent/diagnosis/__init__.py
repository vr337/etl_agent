"""M-L 根因诊断 — 包入口。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-L / 第 5.2 章 execution_runs
覆盖 FR：FR-47（可解释根因诊断：9 类分类 + 阶段 + 可重试性 + 修复建议）
对应 AC：AC-08（硬中断后产出可解释根因）、AC-02（诊断不含错误栈正文）

模块边界：只做「失败事实 → 可解释结论」的映射，不触发任何副作用、不改运行状态机。
诊断结论落 `execution_runs` 的四个既有列（`failure_category` / `failure_stage` /
`retryable` / `diagnosis`），不新增列（禁止事项 7 不改契约）。
"""
