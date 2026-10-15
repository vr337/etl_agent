"""M-H 不可变版本与设计审批 — 包入口（版本链 / 制品冻结 / Diff / 设计审批）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-H / 第 5.2 章 pipeline_versions 与 pipeline_artifacts / 第 6.1 章 API-07
覆盖 FR：FR-26/27/28（版本链、版本 Diff、设计审批）
对应 AC：AC-04（不可变版本摘要）、AC-01（审批职责分离）、AC-17（版本 Diff）
"""
