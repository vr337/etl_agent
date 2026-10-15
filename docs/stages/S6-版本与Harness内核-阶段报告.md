=== 阶段报告 ===
阶段编号：S6
阶段名称：版本与 Harness 安全执行内核
完成时间：2026-10-15T08:41:12+08:00

【读取上下文确认】
- docs/conventions/code-comments.md                        已读
- docs/prd/etl_agent-PRD_v1.1.1-Baseline.md                已读
- docs/spec/etl_agent-SPEC_v1.0.3-Baseline.md              已读
- docs/spec/amendments/AMEND-001-bootstrap-error-codes.md  已读
- docs/spec/amendments/AMEND-002-connection-error-codes.md 已读
- docs/spec/amendments/AMEND-003-profile-file-error-codes.md 已读
- docs/spec/amendments/AMEND-004-generation-error-codes.md 已读
- docs/prompts/S5-生成引擎与确定性门禁.prompt               已读
- docs/stages/S5-生成引擎与确定性门禁-阶段报告.md           已读
- docs/prompts/S6-版本与Harness内核.prompt（本阶段指令）     已读

【前置检查结果（第三节 13 项）】
1 cd 项目根目录 / 2 分支 feat/S6-harness / 3 master 含 S5 合并 + AMEND-004 提交 / 4 工作区干净
/ 5 上游 tag stage-S5-done / 6 postgres+redis healthy / 7 uv 环境 import ok / 8 双库 SELECT 1
/ 9 S1 的 8 张 M6 表全在 / 10 S5 的 generation/gate/llm 三目录在 / 11 基线 + AMEND-001~004 均 Accepted
/ 12 infra/.env 已含 S6 密钥（HARNESS_SIGNING_KEY_HEX 32 字节 hex、HARNESS_PUBLIC_KEYS 1 条 64 hex、
   POLICY_PACKAGE_PATH / POLICY_PACKAGE_SIGNATURE_PATH 均已由人类配置）/ 13 代码规范文件存在。
12 项中 infra/.env 的 LLM/策略包变量为人类配置，WorkBuddy 未写入（禁止事项 20）。

【S5 → S6 吸收清单落实情况】
1 版本链状态机 + 派生 + 乐观锁 + API-07 升级 → 已落地（`versions/state.py`、`service.py`、`artifacts.py`、
  `router.py`；API-07 从幂等读取口升级为写入 + 状态迁移 + 摘要冻结）
2 制品正文继续在 Checkpoint，`pipeline_artifacts` 只存 `{artifact_type, sha256}` → 遵循；
  Diff 的正文级差异经 `generation.service.load_candidate_content` 从 Checkpoint 取回
3 `GENERATABLE_STATUSES` 收敛 → 收敛为 {DRAFT, PROFILING, GENERATED}（PROFILING 保留理由见「口径差异 2」）
4 设计审批独立挂起语义 + ApprovalRequest 接入 → `versions/approval.py`（申请绑定 artifact_sha256）
5 引擎检查保持 stub，接口不变 → `harness/pdp` 与 `gate/engine_check.py` 未动
6 占位符清单正式登记 → `configs/credential_placeholders.yaml`（10 个变量 + 治理约束）
7 GATE-014 主机白名单迁入策略包 → 规则改为从 `package.host_whitelist` 读取，默认值仅作缺省
8 门禁规则全 ERROR，保留 WARN 通道 → `GateResult.warnings` / `SEVERITY_WARN` 已预留未用
9 Mock 候选标定留 S10 → S6 仍用 `MockProvider`（`ETL_AGENT_LLM_PROVIDER=mock`）

【产出文件清单】
新增 77 个文件 + 更新 8 个 = 提交 80 个（+7039 / −181）：
- `src/etl_agent/versions/`（9 个）：M-H 版本链 / 派生 / 乐观锁 / 制品 / Diff / 审批 / 路由 / 模型 / 错误码
- `src/etl_agent/harness/`（29 个）：M-J PDP 9 规则 / 签名策略包 / Prepare / 四眼 / Commit / Capability /
  Tool Broker + Replay Guard / Outbox / 副作用注册表 / 租约 / 路由 / 模型 / 错误码
- `scripts/gen_policy_package.py`：策略包 + Ed25519 分离签名生成脚本
- `configs/policy/harness-v2-20260808.json` + `.sig`：交付的策略包（9 规则 + 5 主机白名单，SHA256 382fb156…）
- `configs/credential_placeholders.yaml`：占位符清单（S5 吸收 6）
- `tests/versions/`（6 个 + conftest）、`tests/harness/`（11 个 + conftest）
- 更新：`config.py`（Harness 配置 + validate_harness）、`main.py`（lifespan 验签策略包 + 注册两路由）、
  `auth/dependencies.py`（`require_actor_for_project`）、`generation/router.py`（移除 API-07，迁出版本域）、
  `generation/service.py`（`load_candidate_content` + GENERATABLE_STATUSES 改从 versions 导入）、
  `generation/nodes/gate.py`（冻结改走 versions 域）、`gate/rules/no_plaintext_credentials.py`
  （抽出 `first_plaintext_credential` 与 PDP 共用）、`.env.example`、`tests/conftest.py`、`tests/helpers.py`
- 移除：`src/etl_agent/generation/artifacts.py`（S5 吸收 1：版本域接管制品冻结，避免两处实现）

【DoD 逐项结果】
- [x] DoD-1 产出文件齐全 —— 第七节目录 23 个路径逐项存在性校验通过
- [x] DoD-2 Alembic 可升可降 —— upgrade head → downgrade base → upgrade head 三段 exit 0；8 张 M6 表齐备
- [x] DoD-3 版本链测试通过 —— `pytest tests/versions -q` → **29 passed**
- [x] DoD-4 Harness 测试通过 —— `pytest tests/harness -q` → **64 passed**
- [x] DoD-5 回归测试通过 —— infrastructure 25 / auth 17 / projects 17 / connections 18 / secrets 28+1skip /
      profiles 31 / files 25 / generation 18+1skip / gate 27；全量 **299 passed, 2 skipped**（与 S5 基线一致）
- [x] DoD-6 AC-01 四眼职责分离 —— `test_four_eyes.py` 8 passed：自批 → HARNESS_409_SELF_APPROVAL；
      同槽二次 → HARNESS_409_ROLE_TAKEN；一人两槽 → SELF_APPROVAL；P0 required_approvals=2；
      无职责 → AUTH_403_ROLE_REQUIRED；DB 双唯一 IntegrityError 兜底
- [x] DoD-7 AC-05 三阶段协议 —— `test_commit.py` 等 23 passed：准备单含影响报告 / 回滚计划 / TTL；
      双唯一审批；Commit 服务端重验指纹与审批事实；签发 Ed25519 Capability TTL ≤300s；
      同事务 ExecutionRun + Outbox PENDING
- [x] DoD-8 AC-05 防重放 —— `test_capability_replay_guard`：二次消费 → HARNESS_409_CAPABILITY_REPLAYED；
      条件 UPDATE 保证并发只有一个事务成功
- [x] DoD-9 PDP 9 条规则 —— `test_pdp.py` 13 passed：全注册 + 聚合（DENY>DEFER>REQUIRE_APPROVAL>ALLOW、
      P0>P1>P2>P3）+ DENY 清空审批要求
- [x] DoD-10 PDP DENY 无放行通道 —— `test_prepare_deny_returns_403`：决策留证、**零准备单**落库
- [x] DoD-11 签名策略包 —— `test_policy_package.py` 8 passed：验签通过；清单篡改 / 签名篡改 / 错公钥 /
      无公钥 / 文件缺失 / rule_id+version 重复 全部拒绝
- [x] DoD-12 Tool Broker 唯一出口 —— `test_tool_broker_registry_only`：未注册命令 →
      HARNESS_403_TOOL_NOT_REGISTERED；注册清单 9 类 + 主执行命令
- [x] DoD-13 事务 Outbox —— `test_outbox_pending_written` + 状态机 PENDING→SENDING→SENT→FAILED
- [x] DoD-14 执行租约 —— `test_lease_mutex`：并发第二次 → HARNESS_409_LEASE_HELD；过期可重取
- [x] DoD-15 版本链禁原地覆盖 + 派生 + 乐观锁 —— `test_no_overwrite_frozen` / `test_derivation_parent_id` /
      `test_optimistic_lock`
- [x] DoD-16 AC-02 凭据零明文 —— `test_zero_plaintext_harness_responses`；正则扫描仅命中 `${}` 占位符与
      测试构造的假值（`s3cr3t-value` 为 GATE-001 故意失败样本）
- [x] DoD-17 Lint / Type 干净 —— ruff `All checks passed!`；mypy `no issues found in 46 source files`
- [x] DoD-18 未修改基线 —— `git diff --name-only` 无 `*-Baseline`（0 命中）
- [x] DoD-19 代码注释合规 —— 130 个源文件扫描，0 个 MISSING HEADER
- [x] DoD-20 上下文读取确认（10 文件）+ 遗留分类归档（AMEND-005 草稿）+ .env 未被修改

【执行偏差与口径说明】
1. **版本域错误码复用 `BIZ_409_STATE_CONFLICT`**（人类已裁决）：提示词 §8.10 列出的
   `BIZ_409_VERSION_STATE_CONFLICT` / `BIZ_409_VERSION_OPTIMISTIC_LOCK` 不再产生；
   状态机不允许、乐观锁冲突、版本未冻结、准备单状态冲突、Commit 指纹重验失败统一复用该码。
   新增 3 个 SPEC 6.4 无法表达的码（AMEND-005 草稿已记录）。
2. **`GENERATABLE_STATUSES` 保留 PROFILING**：S5 吸收 3 说「仅 DRAFT / GENERATED 可再生成」，
   但状态链要求 `PROFILING → GENERATED` 可通行，否则 PROFILING 成为无出口死状态。按状态链保留三态。
3. **Commit 指纹重验的事实来源**（实现期发现并修复的真实缺陷）：重验最初用「Commit 请求提供的内容」
   推算指纹，导致任何带 `side_effect_type` 的准备单都自证不一致。改为从准备单登记的
   `impact_report["requested_input"]`（Prepare 时写入）还原原始入参——登记事实在业务库，
   不依赖调用方重传。
4. **GATE-014 主机白名单默认值保留在规则文件内**：白名单迁入策略包（`host_whitelist`）的同时，
   规则侧保留 `DEFAULT_HOST_WHITELIST` 作为「策略包未提供时」的缺省；缺省即降级，故
   `params_of` 取不到包内参数时返回缺省而不是空集——这是「策略可覆盖但不可被清空」的实现口径。
5. **设计审批的审批人角色**：`versions/approval.py` 接受 `DATA_APPROVER / SECURITY_APPROVER / APPROVER`
   三种 Harness 职责（PRD 2.1 的 Checker 1/2 + 项目审批人 APPROVER），而非仅两种。
6. **S6 不执行副作用**：Tool Broker 的出口是「原子写入 Outbox」；Replay Guard 的消费接口已就绪但
   由 S7 Worker 调用（禁止事项 23/24）。API 层未暴露副作用下发端点（SPEC 6.1 未定义）。

【S7 吸收清单】
1. Outbox 扫描 / 发送 / 重试调度：`harness/outbox/service.py` 的 `claim_next` / `mark_sent` /
   `mark_failed` 已就绪（含 `locked_until` 条件领取），S7 接 Beat 周期任务（SPEC 8：扫描 2s）。
2. Replay Guard 的真实调用点在 Worker：`broker/replay_guard.py::consume` + `broker/tool_broker.py::dispatch`
   已就绪，S7 在拉取 Outbox 后先验签再消费。
3. 执行租约的持久化实现：`harness/lease.py` 为进程内实现，S7 换 DB / Redis 时保持 `LeaseManager` 协议不变。
4. 真实引擎检查：`gate/engine_check.py::EngineChecker` 协议已就位，S7 在 Worker 侧物化运行期变量后
   调 SeaTunnel `--check`，只回传脱敏摘要（FR-20）。
5. 占位符变量名契约：`configs/credential_placeholders.yaml` 与 MockProvider 产出必须与 S7
   `credential_materialization` 一致；不一致会导致运行期物化失败。
6. `harness_policy_decisions.rule_evidence` 目前只写规则 ID / 风险 / 脱敏命中事实；S7 若做证据账本
   （FR-36 哈希链）需与本表的 `policy_package_sha256` 串联。
7. 监督契约预算目前是 `DEFAULT_SUPERVISION_CONTRACT` 常量（FR-45 七项）；S8 监督引擎应按
   `preparation.supervision_contract` 快照执行，并写 `ExecutionRun.supervision_contract_sha256`。
8. API-11（触发受管执行）/ API-12 / API-13 未实现：S7 起由 ExecutionRun 状态机接管。

【AMEND-005 草稿】
（类型 C：新增 3 个 HARNESS_ 错误码；状态 Draft，不自动提交）
文件路径：docs/spec/amendments/AMEND-005-harness-error-codes.md
新增：
| 段 | HTTP | 错误码 | 含义 |
|----|------|--------|------|
| HARNESS_ | 403 | `HARNESS_403_TOOL_NOT_REGISTERED` | 命令不在注册清单（实现/配置错误，非策略拒绝） |
| HARNESS_ | 401 | `HARNESS_401_CAPABILITY_INVALID` | 令牌签名/哈希/claims 绑定不可信 |
| HARNESS_ | 409 | `HARNESS_409_LEASE_HELD` | 执行租约被占用（并发重入） |
复用（不新增）：`BIZ_409_STATE_CONFLICT`、`BIZ_404_NOT_FOUND`、`BIZ_409_IDEMPOTENCY_CONFLICT`、
`BIZ_410_PREPARATION_EXPIRED`、`HARNESS_403_POLICY_DENY`、`HARNESS_409_SELF_APPROVAL`、
`HARNESS_409_ROLE_TAKEN`、`HARNESS_409_ALREADY_COMMITTED`、`HARNESS_409_APPROVAL_INSUFFICIENT`、
`HARNESS_409_CAPABILITY_REPLAYED`、`HARNESS_401_CAPABILITY_EXPIRED`、`AUTH_403_*`。
草稿内另列 3 个待人类裁决问题（401 段位选择、LEASE_HELD 是否独立、版本域统一复用）。
**审核动作**：人类确认后 `git add docs/spec/amendments/AMEND-005-harness-error-codes.md` + `git commit`，状态改为 Accepted。

【git_ship 结果】
- branch: feat/S6-harness（上游 stage-S5-done，起点 393103b）
- commit: 0d052f166cd481ca3e0af24418a681229e2b8804（80 files changed, +7039 / −181）
- tag: stage-S6-done（已推送）
- push: 成功（origin feat/S6-harness 393103b..0d052f1；origin stage-S6-done 新建）
- 未入库（按提示词要求）：AMEND-005 草稿、本阶段报告（后者在第七步单独提交）

【github_ops 结果】
- PR URL: https://github.com/vr337/etl_agent/pull/6
- 标题：[S6] 版本与 Harness 安全执行内核
- 标签：stage:S6 / spec:v1.0.3-Baseline / ac:AC-01 / ac:AC-05（四个标签均已确认存在）
- 里程碑：etl_agent v1.0 实施（描述已追加 S6 完成记录：T-11 / T-12）
- milestone 更新: 成功

【遗留问题】
- AMEND-005 草稿待人类审核入库（阻塞 S7 正式基线合并：否；S7 可先按草稿临时使用）
- 6 处执行偏差 / 口径差异已按更严谨一侧实现并记录（见上），均不阻塞
- `infra/.env` 的 Harness 密钥由人类配置（WorkBuddy 未写入）；若轮换密钥需重新运行
  `scripts/gen_policy_package.py` 并重启控制面（启动期强制验签）
- 其余无

【下一阶段建议】
建议启动 S7（M-K/M-L 运行时调度与质量 / 监督）——对应 AC-06/07/08。S6 无阻塞项：
Outbox / Replay Guard / 租约 / 引擎检查四类接口已就位，S7 只需接 Worker 与 Beat；
请优先按「S7 吸收清单」第 1~5 条确认调度与占位符物化契约。

=== 阶段报告结束 ===
