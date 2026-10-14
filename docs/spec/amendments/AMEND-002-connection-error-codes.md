# AMEND-002：连接域错误码补充

- 状态：Accepted
- 日期：2026-10-14
- 关联：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 6.1 章 API-02/03
- 来源：S3 阶段报告遗留（DoD-15 / 第十七节类型 C）；`src/etl_agent/connections/errors.py` 四个新错误码
- 处置：本追加文件**不改动 v1.0.3-Baseline 正文**；待产出 SPEC v1.0.4-Baseline 时合并到 6.4 章节

## 内容

SPEC 6.4 未定义连接域（API-02 连接登记 / API-03 连通性测试）的错误码。S3 实现时新增：

| 段 | HTTP | 错误码 | 含义 |
|----|------|--------|------|
| BIZ_ | 404 | `BIZ_404_CONNECTION_NOT_FOUND` | 连接不存在（跨项目视同不存在，不泄露存在性，SPEC 6.4 BIZ_404 语义） |
| BIZ_ | 409 | `BIZ_409_CONNECTION_NAME_DUPLICATE` | 项目内连接名重复（FR-04：项目内唯一名） |
| BIZ_ | 422 | `BIZ_422_CONNECTOR_UNSUPPORTED` | 连接器类型暂不支持探查（禁止事项 15：Oracle/SQLServer/Doris/Hive/HDFS 返回明确错误） |
| BIZ_ | 422 | `BIZ_422_PROBE_FAILED` | 连通性测试失败（错误摘要必须脱敏，不含密码，AC-02） |

## 复用（非新增，不计入本追加）

| 段 | HTTP | 错误码 | 用途 |
|----|------|--------|------|
| BIZ_ | 400 | `BIZ_400_VALIDATION` | 轮换策略越界（FR-11：max_age 1–3650、0 < warning < max_age），见 `InvalidRotationPolicy` |
| BIZ_ | 409 | `BIZ_409_STATE_CONFLICT` | 凭据治理状态机非法转换 / Secret 已销毁墓碑拒绝读取 |
| SYS_ | 503 | `SYS_503_DEPENDENCY_UNAVAILABLE` | Vault 模式不可用（Q5/S-Q7 待定） |

## 处理

- 在此之前，S3+ 阶段的错误码清单 = SPEC 6.4 + AMEND-001（Bootstrap）+ **本追加文件（连接域）**；
- 审核动作：人类确认后 `git add docs/spec/amendments/AMEND-002-connection-error-codes.md` + `git commit`，状态改为 Accepted；
- **本草稿不随 S3 提交自动入库**（禁止事项 20：AMEND 草稿由人类审核后手动提交）。
