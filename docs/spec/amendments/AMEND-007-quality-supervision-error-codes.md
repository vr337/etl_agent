# AMEND-007：质量发布与监督回滚域错误码补充

- 状态：Accepted
- 日期：2026-10-16
- 关联：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 3.2 章 M-K/M-L / 第 6.1 章 API-13 / 第 8 章
- 来源：S8 阶段报告（DoD-19 / 提示词 §8.14）
- 处置：本追加文件**不改动 v1.0.3-Baseline 正文**；待产出 SPEC v1.0.4-Baseline 时合并到 6.4 章节

## 一、S8 新增（6 个）

| 段 | HTTP | 错误码 | 含义 | 定义位置 |
|----|------|--------|------|---------|
| BIZ_ | 404 | `BIZ_404_ROLLBACK_NOT_FOUND` | 回滚申请 / 制品不存在或跨项目 | `src/etl_agent/rollback/errors.py::RollbackNotFound` |
| BIZ_ | 409 | `BIZ_409_ROLLBACK_STATE_CONFLICT` | 回滚状态机不允许当前操作（未审批即执行 / 终态重复操作 / 制品元数据无效） | `src/etl_agent/rollback/errors.py::RollbackStateConflict` |
| BIZ_ | 422 | `BIZ_422_SWAP_SCHEMA_MISMATCH` | 影子表与正式表 Schema 不一致（同 Schema 强校验，FR-43） | `src/etl_agent/execution/errors.py::SwapSchemaMismatch` |
| BIZ_ | 422 | `BIZ_422_SWAP_QUARANTINED` | 运行已被 QUARANTINE，禁止原子 Swap（AC-07/AC-08 联动红线） | `src/etl_agent/execution/errors.py::SwapQuarantined` |
| BIZ_ | 422 | `BIZ_422_SWAP_QUALITY_FAILED` | 质量报告未 PASSED，禁止原子 Swap | `src/etl_agent/execution/errors.py::SwapQualityFailed` |
| BIZ_ | 422 | `BIZ_422_DIAGNOSIS_UNAVAILABLE` | 根因诊断不可用（运行尚无失败事实，无法分类） | `src/etl_agent/diagnosis/errors.py::DiagnosisUnavailable` |

新增理由：六者都是 SPEC 6.4 既有码表**无法表达**的发布 / 监督 / 回滚期失败类别。

- `BIZ_404_ROLLBACK_NOT_FOUND` 与通用 `BIZ_404_NOT_FOUND` 的差异在**检索维度**：
  回滚申请是 API-13 的独立资源，前端需要区分「回滚不存在」与「运行不存在」
  （`BIZ_404_EXECUTION_NOT_FOUND`，AMEND-006），两者都要 404 但指向不同实体；
- `BIZ_409_ROLLBACK_STATE_CONFLICT` 与通用 `BIZ_409_STATE_CONFLICT` 的差异在**处置路径**：
  前者指向「回滚状态机」（未审批 / 终态），后者指向「运行状态机 / 版本状态机」。
  另：制品元数据无效（`INVALID_METADATA`）也复用本码——其处置与状态冲突一致
  （修元数据后重试），不单设码；
- **三个 Swap 码必须分开**，它们的处置完全不同：
  - `QUARANTINED` = 监督判决结果，处置是「先解除隔离并复盘」，与质量阈值无关；
  - `QUALITY_FAILED` = 契约判定结果，处置是「看质量报告 + 改 HOCON / 修数据后重跑」；
  - `SCHEMA_MISMATCH` = 影子表结构问题，处置是「重建影子表」（swap 会把影子表结构
    变成正式表结构，Schema 不同等于静默改表，属最危险形态）。
  混为一码会让运行中心把「人工判决」与「契约判决」混在同一个「下一步建议」里；
- `BIZ_422_DIAGNOSIS_UNAVAILABLE` 描述的是**诊断服务的前置缺失**（运行尚无失败事实），
  而不是诊断本身失败——它是「现在还没得可诊断」，不是「诊断坏了」。

## 二、S8 复用（不新增，记录决策依据）

| 段 | HTTP | 错误码 | 用途 | 决策说明 |
|----|------|--------|------|---------|
| BIZ_ | 409 | `BIZ_409_STATE_CONFLICT` | 回滚申请指向的运行状态不允许回滚（QUEUED/RUNNING 等）；监督动作与运行状态冲突（终态不可再减损） | SPEC 6.4 已在 API-13 契约明列；监督侧同属「运行状态机不允许」 |
| BIZ_ | 404 | `BIZ_404_EXECUTION_NOT_FOUND` | 回滚申请绑定的运行不存在或跨项目 | AMEND-006 |
| HARNESS_ | 403 | `HARNESS_403_POLICY_DENY` | 回滚准备单 PDP DENY（跨项目 / Secret 外发类） | SPEC 6.4（API-13 契约明列，S6 复用） |
| HARNESS_ | 409 | `HARNESS_409_APPROVAL_INSUFFICIENT` / `_SELF_APPROVAL` / `_ROLE_TAKEN` | 回滚四眼审批不足 / 自批 / 槽被占 | SPEC 6.4（S6 复用；回滚走同一 Harness 协议） |
| SYS_ | 503 | `SYS_503_DEPENDENCY_UNAVAILABLE` | 外部减损动作依赖不可用（引擎 stop-job 不可达） / Doris 不可达（Swap） | 基础设施依赖失败，与 Redis 交接通道同类 |

复用理由：
- 原子 Swap 与受管回滚都**落在既有资源状态机上**，没有引入新的状态语义，
  因此状态冲突错误码不需要新增；
- `ROLLBACK_DORIS` / `CLEANUP_DORIS` 是**副作用类型**，不是新的资源，
  它们的审批失败直接复用 S6 的 Harness 错误码（禁止事项 17：回滚不得绕过 Harness 协议）；
- 外部减损动作（stop-job / pause-job）与 Doris Swap 的依赖不可达同属基础设施类
  （`SYS_503_DEPENDENCY_UNAVAILABLE`）——可重试，非配置错误。

## 三、待人类裁决

1. **404 双义是否接受**：`BIZ_404_ROLLBACK_NOT_FOUND` 承载「回滚不存在」与
   「制品不存在」两义（与 AMEND-006 的 404 双义口径一致）。若要严格区分，
   需再拆一个码（本阶段按「统一 404 不泄露存在性」的既有口径处理，AC-12）。
2. **`INVALID_METADATA` 是否单设码**：制品元数据无效当前复用
   `BIZ_409_ROLLBACK_STATE_CONFLICT`（处置一致）。若要独立表达「元数据坏 vs 状态坏」，
   需新增 `BIZ_409_ROLLBACK_INVALID_METADATA`。
3. **Doris 不可达是否与引擎不可达合并**：两者当前同为 `SYS_503_DEPENDENCY_UNAVAILABLE`。
   若运维要求区分「引擎挂了」与「Doris 挂了」，需拆分基础设施子码。
4. **`BIZ_422_DIAGNOSIS_UNAVAILABLE` 的段位**：当前定 422（请求合法但当前不可诊断）。
   若按「资源尚不存在」的语义，也可归 404——本阶段按「运行已存在、只是无失败事实」定 422。

## 口径裁决（S8 收尾确认）

1. `BIZ_404_ROLLBACK_NOT_FOUND` 双义接受（回滚不存在 + 制品不存在），用 message 区分；
2. `INVALID_METADATA` **不单设码**，复用 `BIZ_409_ROLLBACK_STATE_CONFLICT`（处置一致）；
3. Doris 不可达与引擎不可达**保持现状**（均归 `SYS_503_DEPENDENCY_UNAVAILABLE`）；
   若 S10 前需精细监控，再走 AMEND 流程拆分；
4. `BIZ_422_DIAGNOSIS_UNAVAILABLE` 保持 **422**（运行存在、仅无失败事实）。
