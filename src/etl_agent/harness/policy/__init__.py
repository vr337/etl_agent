"""M-J Harness 安全执行内核 — 签名策略包。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 8 章「安全 · 授权与职责分离」
覆盖 FR：FR-30（策略清单 + SHA256 + Ed25519 分离签名；启动与热切换前强制验签）
对应 AC：AC-01、AC-05

`configs/policy/` 下的清单与 `.sig` 由 `scripts/gen_policy_package.py` 生成，
控制面只加载并验签，不持有私钥。
"""
