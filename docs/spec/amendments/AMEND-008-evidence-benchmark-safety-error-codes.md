# AMEND-008：审计证据与评测安全进化域错误码补充

- 状态：Accepted
- 日期：2026-10-17
- 关联：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 3.2 章 M-M/M-N / 第 6.1 章 API-15 / 第 8 章
- 来源：S9 阶段报告（DoD-21 / 提示词 §8.10）
- 处置：本追加文件**不改动 v1.0.3-Baseline 正文**；待产出 SPEC v1.0.4-Baseline 时合并到 6.4 章节

## 一、S9 新增（10 个）

| 段 | HTTP | 错误码 | 含义 | 定义位置 |
|----|------|--------|------|---------|
| BIZ_ | 404 | `BIZ_404_EVIDENCE_STREAM_NOT_FOUND` | 项目证据流不存在（项目尚无任何证据事件） | `src/etl_agent/evidence/errors.py::EvidenceStreamNotFound` |
| BIZ_ | 409 | `BIZ_409_EVIDENCE_CHAIN_BROKEN` | 证据链断链：流头 `head_hash` 与按事件重算的 `previous_hash` 不一致（有人在链外改过数据） | `src/etl_agent/evidence/errors.py::EvidenceChainBroken` |
| BIZ_ | 422 | `BIZ_422_EVIDENCE_VERIFY_FAILED` | 整流验证失败：`event_hash` / `payload_sha256` / 序号连续性任一项不符 | `src/etl_agent/evidence/errors.py::EvidenceVerifyFailed` |
| BIZ_ | 404 | `BIZ_404_BENCHMARK_RUN_NOT_FOUND` | Benchmark 运行记录不存在或跨项目 | `src/etl_agent/benchmark/errors.py::BenchmarkRunNotFound` |
| BIZ_ | 409 | `BIZ_409_BENCHMARK_STATE_CONFLICT` | Benchmark 套件冻结校验失败（manifest / 制品 SHA256 与冻结值不符）或运行状态冲突 | `src/etl_agent/benchmark/errors.py::BenchmarkStateConflict` |
| BIZ_ | 422 | `BIZ_422_BENCHMARK_GATE_FAILED` | Benchmark 六项门禁任一未达标（运行本身成功执行，但判定 FAIL） | `src/etl_agent/benchmark/errors.py::BenchmarkGateFailed` |
| BIZ_ | 404 | `BIZ_404_SAFETY_PROPOSAL_NOT_FOUND` | 安全改进提案不存在或跨项目 | `src/etl_agent/safety/errors.py::SafetyProposalNotFound` |
| BIZ_ | 409 | `BIZ_409_SAFETY_STATE_CONFLICT` | 提案 / 发布状态机不允许当前操作（**状态跳跃**如 DRAFT→APPROVED、终态重复操作、未影子即小流量） | `src/etl_agent/safety/errors.py::SafetyStateConflict` |
| BIZ_ | 422 | `BIZ_422_SAFETY_CANARY_OUT_OF_RANGE` | 小流量百分比越界（SPEC 固定 1%–10%） | `src/etl_agent/safety/errors.py::SafetyCanaryOutOfRange` |
| BIZ_ | 422 | `BIZ_422_SAFETY_SIGNATURE_INVALID` | 发布清单 / 检查点 Ed25519 签名验签失败 | `src/etl_agent/safety/errors.py::SafetySignatureInvalid` |

新增理由：十者都是 SPEC 6.4 既有码表**无法表达**的证据完整性 / 评测判定 / 安全进化期失败类别。

- **证据三码体现「三个不同的处置动作」**，不能合并：
  - `BIZ_404_EVIDENCE_STREAM_NOT_FOUND` = 这个项目**还没有证据**（正常状态，不是故障），
    处置是「继续跑、等事件落链」，绝不能报警；
  - `BIZ_409_EVIDENCE_CHAIN_BROKEN` = **完整性事故**，处置是「立即停写、冻结现场、人工比对
    离线备份定位被篡改的那一条」，与 `VERIFY_FAILED` 的区别在于：断链是「追加路径当场发现
    链头对不上」（写入期），必须阻断后续追加，属 409 冲突；
  - `BIZ_422_EVIDENCE_VERIFY_FAILED` = **离线验证不通过**，处置是「输出 errors[] 明细
    （哪条序号、哪个字段不符）交人工审计」，读取期不阻断写入（验证是只读操作）。
- `BIZ_404_SAFETY_PROPOSAL_NOT_FOUND` 与通用 `BIZ_404_NOT_FOUND` 的差异在**检索维度**：
  提案是安全进化的独立资源，前端要区分「提案不存在」与「发布不存在」
  （同一域内两类独立资源，本阶段发布视图由提案路径承载，未单设
  `BIZ_404_SAFETY_RELEASE_NOT_FOUND`——见待裁决 1）。
- `BIZ_409_SAFETY_STATE_CONFLICT` 与通用 `BIZ_409_STATE_CONFLICT` 的差异在**处置路径**：
  前者指向「提案 / 发布状态机」（含**不可跳跃**红线），后者指向「运行 / 版本 / 回滚状态机」。
  状态跳跃（DRAFT 直接到 APPROVED）与终态重复操作**同码**——两者的处置都是
  「回到合法状态重走流程」，且都是**服务端强约束**不可被客户端绕过。
- `BIZ_422_SAFETY_CANARY_OUT_OF_RANGE` 与 `BIZ_409_SAFETY_STATE_CONFLICT` **必须分开**：
  越界是**参数合法性问题**（客户端传了 0% 或 50%，改参数即可），
  状态冲突是**流程顺序问题**（改了参数也没用，得先走影子）。混为一码会让前端
  给出错误的「下一步建议」。
- `BIZ_422_SAFETY_SIGNATURE_INVALID` 与证据域的签名失败同族但**域独立**：
  本码专指**发布清单**与**发布期检查点**的验签失败（晋升授权红线），
  与 S6 的策略包验签（`HARNESS_403_*` 族）不是同一物；签名失败一律 422
  （请求本身合法、制品不合法），**不可降级为放行**。

## 二、S9 复用（不新增，记录决策依据）

| 段 | HTTP | 错误码 | 用途 | 决策说明 |
|----|------|--------|------|---------|
| AUTH_ | 403 | `AUTH_403_ROLE_REQUIRED` | 非 SECURITY_APPROVER 裁决安全提案 / 授权晋升（AC-01 职责分离） | SPEC 6.4 既有；安全维度独立于项目 ADMIN，**不隐式升级** |
| HARNESS_ | 403 | `HARNESS_403_POLICY_DENY` | L0 静态注入场景的 PDP DENY（明文凭据 / 破坏性命令 / Secret 外发） | SPEC 6.4（API-03 契约明列，S6 复用） |
| HARNESS_ | 409 | `HARNESS_409_APPROVAL_INSUFFICIENT` / `_SELF_APPROVAL` / `_ROLE_TAKEN` | L1 模拟故障场景的审批类减损判定 | SPEC 6.4（S6 复用；评测复用同一 Harness 协议） |
| BIZ_ | 409 | `BIZ_409_STATE_CONFLICT` | 评测 / 证据链路中运行状态机冲突（非 Benchmark / 非安全域自身状态机） | SPEC 6.4 既有 |
| SYS_ | 503 | `SYS_503_DEPENDENCY_UNAVAILABLE` | 引擎 / Doris 不可达导致 L2 真实链路场景无法完成 | 基础设施依赖失败，与 S7/S8 同类 |
| SYS_ | 500 | `SYS_500_INTERNAL` | 审计写入失败（`AuditWriteFailure`，**只记事件不阻断业务**） | 审计不可成为业务失败源（禁止事项：审计失败由事件流承载） |

复用理由：
- 评测（L0/L1/L2）与安全进化**不引入新的资源状态机**：L0 的攻击判定直接复用既有 PDP
  规则与错误码（`HARNESS_403_POLICY_DENY`），这正是「评测即真实链路」的证明——
  若评测需要专用错误码，说明评测没走真链路；
- 安全提案的裁决权限复用 `AUTH_403_ROLE_REQUIRED`，而不是新设「安全域 403」：
  AC-01 的本质是**职责分离**，即「这个角色没这个职责」，与既有 403 语义完全一致；
- `SYS_500_INTERNAL` 复用是**有意为之的设计红线**：审计写入失败（如 DB 抖动）
  **绝不能让业务请求失败**（`record_safely` 吞异常并落到备用通道），
  故它只出现在系统日志与监控，不构成对客户端的独立错误语义。

## 三、待人类裁决

1. **发布资源是否单设 404 码**：`safety_releases` 是独立表（SPEC 5.2），当前发布视图
   挂在提案路径下（`/proposals/{id}/releases`），故未设
   `BIZ_404_SAFETY_RELEASE_NOT_FOUND`。若后续 API-15 暴露独立
   `/releases/{release_id}` 路径，需按 AMEND-007 的「独立资源独立码」口径补一个。
2. **`BIZ_409_BENCHMARK_STATE_CONFLICT` 是否拆分**：当前承载「套件冻结校验失败」与
   「运行状态冲突」两义（都属 409）。若要求前端区分「套件被改」与「运行状态不对」，
   需拆 `BIZ_409_BENCHMARK_SUITE_TAMPERED`。
3. **`BIZ_422_EVIDENCE_VERIFY_FAILED` 是否细分**：当前 `errors[]` 明细内已区分
   「hash 不符 / 序号断档 / 签名无效」三类。若运维要求按类别分码告警，需拆三码
   （本阶段按「统一 422 + 明细数组」处理，保持码表精简）。
4. **L2 真实链路是否需专用错误码**：当前 L2 复用 `SYS_503_DEPENDENCY_UNAVAILABLE`
   （引擎不可达）。若评测结果需要区分「评测未跑」与「评测跑了但依赖挂了」，
   需新增评测域状态码。

## 口径裁决（S9 收尾确认）

1. 发布资源**不单设 404 码**——发布视图挂在提案路径下，非独立资源；若 S10 暴露独立 `/releases/{id}` 再走 AMEND。
2. Benchmark 状态冲突**不拆分套件篡改码**——套件篡改与状态冲突同为 409，处置一致；前端用 message 区分。
3. 证据验证失败**不按类别细分**——`errors[]` 明细已区分三类；统一 422 + 明细数组。
4. L2 **不加专用错误码**——复用 `SYS_503_DEPENDENCY_UNAVAILABLE`；Benchmark 结果是报告的一部分。
