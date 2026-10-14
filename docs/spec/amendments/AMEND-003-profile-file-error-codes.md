# AMEND-003：探查域与文件资产域错误码补充

- 状态：Accepted
- 日期：2026-10-14
- 关联：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 6.1 章 API-04
- 来源：S4 阶段报告遗留（DoD-15 / 第十七节类型 C）；`src/etl_agent/profiles/errors.py` 与 `src/etl_agent/files/errors.py` 共 6 个新错误码
- 处置：本追加文件**不改动 v1.0.3-Baseline 正文**；待产出 SPEC v1.0.4-Baseline 时合并到 6.4 章节

## 内容

SPEC 6.4 未定义元数据探查域（API-04）与文件资产域（M-E）的错误码。S4 实现时新增：

| 段     | HTTP | 错误码                                 | 含义                                                   |
| ----- | ---- | ----------------------------------- | ---------------------------------------------------- |
| BIZ\_ | 404  | `BIZ_404_PROFILE_NOT_FOUND`         | Profile 不存在（跨项目视同不存在，不泄露存在性，SPEC 6.4 BIZ_404 语义）     |
| BIZ\_ | 404  | `BIZ_404_FILE_ASSET_NOT_FOUND`      | 文件资产不存在（跨项目视同不存在，不泄露存在性）                             |
| BIZ\_ | 422  | `BIZ_422_FILE_TOO_LARGE`            | 单文件超过大小上限（FR-13：默认 25 MiB；写入过程中累加判定，超限即中断）           |
| BIZ\_ | 422  | `BIZ_422_FILE_TOO_MANY_ROWS`        | 单文件超过行数上限（FR-13：默认 100,000 行；读取时增量计数，超限即中断）          |
| BIZ\_ | 422  | `BIZ_422_FILE_FORMAT_UNSUPPORTED`   | 格式不在 9 种受支持格式之内，或内容无法按该格式解析（FR-13）                   |
| BIZ\_ | 422  | `BIZ_422_SAMPLE_LIMIT_OUT_OF_RANGE` | Profile 采样上限越界（FR-12：sample_limit 1–100，越界拒绝、不做静默截断） |

## 复用（非新增，不计入本追加）

| 段      | HTTP | 错误码                             | 用途                                                                   |
| ------ | ---- | ------------------------------- | -------------------------------------------------------------------- |
| BIZ\_  | 400  | `BIZ_400_VALIDATION`            | 端点角色非法（非 SOURCE/SINK）、源表/表名标识符非法                                     |
| BIZ\_  | 404  | `BIZ_404_CONNECTION_NOT_FOUND`  | 探查引用的连接不存在或跨项目（AMEND-002）                                            |
| BIZ\_  | 422  | `BIZ_422_CONNECTOR_UNSUPPORTED` | 连接器类型无元数据探查通道（Oracle/SQLServer/Doris/Hive/HDFS/LOCAL_FILE，AMEND-002） |
| BIZ\_  | 422  | `BIZ_422_PROBE_FAILED`          | 源库探查失败（错误摘要经 `connections.probe.mask_error` 脱敏，AMEND-002）            |
| AUTH\_ | 403  | `AUTH_403_NOT_PROJECT_MEMBER`   | 非项目成员访问（SPEC 6.4 / S2）                                               |

## 与 S4 提示词的一处口径差异（需人类裁决）

S4 提示词第十节用例表将 `test_sample_limit_range` 的期望写为「越界 → 400」，  
而第九节 9.6 的错误码清单为 `BIZ_422_SAMPLE_LIMIT_OUT_OF_RANGE 422`。  
实现取 **422**：错误码名内嵌 `422` 段位，返回 400 会自相矛盾，且 422 与 FastAPI  
的参数校验语义一致。若人类裁定应以 400 为准，需同时改错误码名（`BIZ_400_...`）  
并回刷本追加件与测试。

## 处理

- 在此之前，S4+ 阶段的错误码清单 = SPEC 6.4 + AMEND-001（Bootstrap）+ AMEND-002（连接域）+ **本追加文件（探查域 / 文件资产域）**；
- 审核动作：人类确认后 `git add docs/spec/amendments/AMEND-003-profile-file-error-codes.md` + `git commit`，状态改为 Accepted；
- **本草稿不随 S4 提交自动入库**（S4 禁止事项 19：AMEND 草稿由人类审核后手动提交）。

## 口径裁决（S4 收尾确认）

1. `BIZ_422_SAMPLE_LIMIT_OUT_OF_RANGE` 返回 422（接受实现；提示词里的"400"为笔误）；
2. 跨项目引用他人连接返回 `BIZ_404_CONNECTION_NOT_FOUND`（延续"不泄露存在性"语义）；
   跨项目非成员访问仍为 `AUTH_403_NOT_PROJECT_MEMBER`。