# AMEND-001：Bootstrap 错误码补充

- 状态：Accepted
- 日期：2026-10-14
- 关联：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章
- 来源：S2 阶段报告遗留 3

## 内容

SPEC 6.2 未定义 API-01（Bootstrap）的错误码。S2 实现时新增：

| 段 | HTTP | 错误码 | 含义 |
|----|------|--------|------|
| BIZ_ | 403 | `BIZ_403_BOOTSTRAP_DISABLED` | 工作区已禁用 Bootstrap |
| BIZ_ | 403 | `BIZ_403_BOOTSTRAP_TOKEN_INVALID` | Bootstrap Token 缺失或错误 |

## 处理

- 本追加文件**不改动 v1.0.3-Baseline 正文**；
- 待产出 SPEC v1.0.4-Baseline 时合并到 6.4 章节；
- 在此之前，S3+ 阶段的错误码清单 = SPEC 6.4 + 本追加文件。