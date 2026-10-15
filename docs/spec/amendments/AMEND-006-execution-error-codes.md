# AMEND-006：执行域与引擎域错误码补充

- 状态：Accepted
- 日期：2026-10-15
- 关联：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 3.2 章 M-J/M-K / 第 6.1 章 API-11/12/14
- 来源：S7 阶段报告（DoD-22 / 提示词 §8.12）
- 处置：本追加文件**不改动 v1.0.3-Baseline 正文**；待产出 SPEC v1.0.4-Baseline 时合并到 6.4 章节

## 一、S7 新增（6 个）

| 段 | HTTP | 错误码 | 含义 | 定义位置 |
|----|------|--------|------|---------|
| BIZ_ | 404 | `BIZ_404_EXECUTION_NOT_FOUND` | 执行运行 / 准备单不存在或跨项目；质量报告尚未生成（执行未达终态） | `src/etl_agent/execution/errors.py::ExecutionNotFound` |
| BIZ_ | 409 | `BIZ_409_EXECUTION_STATE_CONFLICT` | ExecutionRun 状态机不允许当前操作（终态重复触发 / 准备单未 Commit / 声明版本与登记不一致） | `src/etl_agent/execution/errors.py::ExecutionStateConflict` |
| BIZ_ | 422 | `BIZ_422_SEATUNNEL_SUBMIT_FAILED` | SeaTunnel 作业提交被拒（摘要脱敏，不含 HOCON 正文） | `src/etl_agent/execution/errors.py::SeatunnelSubmitFailed` |
| BIZ_ | 422 | `BIZ_422_SEATUNNEL_CHECK_FAILED` | 引擎侧 `--check` 校验未通过 / CLI 不可用 / 校验超时 | `src/etl_agent/execution/errors.py::SeatunnelCheckFailed` |
| BIZ_ | 422 | `BIZ_422_CREDENTIAL_MATERIALIZATION_FAILED` | 占位符物化失败：契约外变量、缺取值、替换后仍有残留 | `src/etl_agent/execution/errors.py::CredentialMaterializationFailed` |
| BIZ_ | 503 | `BIZ_503_SEATUNNEL_UNAVAILABLE` | 引擎不可达（连接 / 超时；可重试类，非配置错误） | `src/etl_agent/execution/errors.py::SeatunnelUnavailable` |

新增理由：六者都是 SPEC 6.4 既有码表**无法表达**的执行期失败类别。

- 「执行不存在」与通用 `BIZ_404_NOT_FOUND` 的差异在于**响应语义**：执行运行是
  运行中心的高频查询对象，前端需要区分「这条运行不存在」与「质量报告还没生成」，
  两者都是 404 但检索维度不同（S7 采用同一码承载两义，见「待人类裁决」1）；
- 「执行状态冲突」与通用 `BIZ_409_STATE_CONFLICT` 的差异在于**处置路径**：
  前者指向「去看运行状态机」（重试要新建运行，FR-42），后者指向「去看资源状态机」；
- 引擎三码必须与配置类失败分开：`SUBMIT_FAILED`（引擎拒绝，要改配置 / 改 HOCON）、
  `CHECK_FAILED`（门禁前置失败，不该提交）、`UNAVAILABLE`（网络类，重试即可）——
  三者在运行中心要呈现为不同的「下一步建议」，混为一码会让运维只能猜；
- 「物化失败」是 AC-02 红线的**唯一可观测出口**：物化失败必须显式失败
  （禁止半物化提交引擎），且错误摘要绝不能带出占位符对应的真实值。

## 二、S7 复用（不新增，记录决策依据）

| 段 | HTTP | 错误码 | 用途 | 决策说明 |
|----|------|--------|------|---------|
| SYS_ | 503 | `SYS_503_DEPENDENCY_UNAVAILABLE` | Capability 令牌交接通道（Redis）不可达 | 交接通道是**基础设施依赖**而非业务状态；失败即拒绝执行（不降级为无令牌执行，FR-34） |
| BIZ_ | 400 | `BIZ_400_VALIDATION` | 调度策略参数非法（cron / 时区 / 模式 / misfire 宽限 / 水印契约） | 调度域**不新增码**：与其余域的参数校验语义完全一致 |
| BIZ_ | 404 | `BIZ_404_NOT_FOUND` | 调度策略不存在或跨项目 | 不泄露存在性（AC-12） |
| BIZ_ | 409 | `BIZ_409_STATE_CONFLICT` | 管线不存在 / 跨项目（调度策略绑定前置） | 状态机不允许 |
| HARNESS_ | 401/409 | `HARNESS_401_CAPABILITY_INVALID` / `HARNESS_409_CAPABILITY_REPLAYED` | Worker 侧验签失败 / 令牌二次消费 | S6 已定义（AMEND-005），Worker 侧直接复用 |
| HARNESS_ | 403 | `HARNESS_403_POLICY_DENY` | 调度触发创建准备单时 PDP DENY | S6 已定义；调度**不绕过策略**，DENY 即本槽位不触发 |
| AUTH_ | 403 | `AUTH_403_ROLE_REQUIRED` | API-11 非 OPERATOR | SPEC 6.4 已定义 |
| AUTH_ | 403 | `AUTH_403_NOT_PROJECT_MEMBER` | API-12/14 非项目成员 | SPEC 6.4 已定义 |
| SYS_ | 500 | `SYS_500_INTERNAL` | 未处理异常（响应附 trace_id，栈只进日志） | SPEC 6.4 已定义 |

## 三、启动期失败（非 API 错误码，不入错误码体系）

S7 新增一条启动期强制校验（`config.py::validate_worker`），失败即拒绝启动：

- 四类周期任务间隔 / 引擎超时非正数；
- `ETL_AGENT_SEATUNNEL_URL` 未配置（执行面唯一引擎出口，ADR-01）；
- `ETL_AGENT_OTEL_ENABLED=true` 但未配置 OTLP 端点（FR-60：开关开而不配置端点等于静默丢弃遥测）。

理由与 S6 一致：这些配置错误在运行期表现为「任务永不触发」「遥测静默丢失」，
在启动期拒绝比在生产里慢慢发现便宜得多。

## 四、待人类裁决

1. `BIZ_404_EXECUTION_NOT_FOUND` 同时承载「运行不存在」与「质量报告尚未生成」两义——
   是否接受？（备选：为后者新增 `BIZ_404_QUALITY_REPORT_NOT_FOUND`）
2. `BIZ_422_CREDENTIAL_MATERIALIZATION_FAILED` 的段位选择：物化失败本质是**配置 / 契约问题**
   （占位符与连接不匹配），选 422 而非 409 是否合适？
3. `BIZ_503_SEATUNNEL_UNAVAILABLE` 与 `SYS_503_DEPENDENCY_UNAVAILABLE` 是否应合并
   （两者都是「外部依赖不可达」，但前者可用于按引擎维度做 SLA 统计）。

## 处理

- 在此之前，S7+ 阶段的错误码清单 = SPEC 6.4 + AMEND-001 ~ AMEND-005 + **本追加文件（执行 / 引擎域）**；
- 审核动作：人类确认后 `git add docs/spec/amendments/AMEND-006-execution-error-codes.md` +
  `git commit`，状态改为 Accepted；
- **本草稿不随 S7 提交自动入库**（S7 禁止事项 22：AMEND 草稿由人类审核后提交）。

## 口径裁决（S7 收尾确认）

1. `BIZ_404_EXECUTION_NOT_FOUND` 双义接受（运行不存在 + 质量报告未生成），用 message 区分；
2. `BIZ_422_CREDENTIAL_MATERIALIZATION_FAILED` 保持 422（配置/契约问题，非状态冲突）；
3. `BIZ_503_SEATUNNEL_UNAVAILABLE` 独立设码，不与 `SYS_503_DEPENDENCY_UNAVAILABLE` 合并（便于按引擎维度做 SLA）。
