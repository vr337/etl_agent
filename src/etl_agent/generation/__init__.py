"""M-F 生成引擎 — 包入口（LangGraph 状态机 / Checkpoint / 遥测 / API）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-F / 第 5.3 章状态隔离 / 第 6.1 章 API-05/06/07
覆盖 FR：FR-16/17/18/67/68（运行生命周期、澄清恢复、生成、状态隔离）
对应 AC：AC-03（中断与恢复）、AC-13（三层状态隔离）
"""
