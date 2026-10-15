=== 阶段报告 ===
阶段编号：S6
阶段名称：版本与 Harness 安全执行内核
完成时间：2026-10-15T08:41:12+08:00

【读取上下文确认】
- docs/conventions/code-comments.md                        已读
- docs/prd/etl_agent-PRD_v1.1.1-Baseline.md                已读（重点 FR-26~37 / AC-01 / AC-05 / PRD 2.3 职责分离红线）
- docs/spec/etl_agent-SPEC_v1.0.3-Baseline.md              已读（重点 3.2 M-H/M-J / 5.2 / 6.1 API-07~10 / 6.4 / 7.1 / 8 / A.1）
- docs/spec/amendments/AMEND-001-bootstrap-error-codes.md  已读
- docs/spec/amendments/AMEND-002-connection-error-codes.md 已读
- docs/spec/amendments/AMEND-003-profile-file-error-codes.md 已读
- docs/spec/amendments/AMEND-004-generation-error-codes.md 已读
- docs/prompts/S5-生成引擎与确定性门禁.prompt               已读
- docs/stages/S5-生成引擎与确定性门禁-阶段报告.md           已读（作为本报告格式与口径参照）
- docs/prompts/S6-版本与Harness内核.prompt（本阶段指令）     已读

【前置检查结果（第三节 13 项）】
13 项全部满足，无一项需要「按偏差方式满足」：
1~5：cd 项目根成功；当前分支 feat/S6-harness（上游 stage-S5-done）；master log 含
  `b45375a [S5] 生成引擎与确定性门禁 (#5)` 与 `becf977 AMEND-004 审核通过`；工作区干净；
  上游 tag stage-S5-done 存在。
6：`docker compose -f infra/docker-compose.yml ps` → etl_agent_postgres（healthy）/ etl_agent_redis（healthy）。
7：`uv run python -c "import etl_agent"` → ok。
8：双库 `SELECT 1` 均返回 1（etl_agent / etl_checkpoint）。
9：M6 的 8 张表全部存在（harness_policy_decisions / execution_preparations /
  harness_approval_decisions / harness_capability_grants / harness_command_outbox /
  harness_side_effect_preparations / harness_side_effect_approvals /
  harness_side_effect_capability_grants）。
10：S5 的 generation / gate / llm 三目录均在。
11：基线（PRD v1.1.1-Baseline / SPEC v1.0.3-Baseline）与 AMEND-001~004 均 Accepted。
12：`infra/.env` 已由人类配好 S6 密钥——ETL_AGENT_HARNESS_SIGNING_KEY_HEX（32 字节 hex）、
  ETL_AGENT_HARNESS_PUBLIC_KEYS（1 条 64 hex）、ETL_AGENT_POLICY_PACKAGE_PATH /
  _SIGNATURE_PATH（指向 configs/policy/harness-v2-20260808.*）。
  WorkBuddy 全程未写入 `infra/.env`（禁止事项 20）。
13：docs/conventions/code-comments.md 存在。

【执行偏差与口径说明（均已获人类确认或按更严谨一侧实现）】
1. **版本域错误码统一复用 `BIZ_409_STATE_CONFLICT`**（人类在启动前裁决）：提示词 §8.10 曾列出
   `BIZ_409_VERSION_STATE_CONFLICT` / `BIZ_409_VERSION_OPTIMISTIC_LOCK` 两个「新增？」候选。
   裁决为不新增：版本状态机不允许、乐观锁 `lock_version` 冲突、版本未冻结、准备单状态机不允许、
   Commit 指纹重验失败五种场景对调用方的处置完全相同（重新读取资源后重试），单独设码只增加契约面。
   仅保留 3 个 SPEC 6.4 无法表达的失败类别（见 AMEND-005 草稿）。
2. **`GENERATABLE_STATUSES` 保留 PROFILING**：S5 吸收 3 写「仅 DRAFT / GENERATED 可再生成」，
   但版本链要求 `PROFILING → GENERATED` 可通行，否则 PROFILING 成为无出口死状态。按状态链保留三态。
3. **Commit 指纹重验的事实来源**（实现期发现并修复的**真实缺陷**）：最初用「Commit 请求提供的内容」
   推算输入指纹，导致任何带 `side_effect_type` 的准备单都自证不一致（`test_commit_atomic_outbox`
   首轮即暴露）。改为从准备单登记的 `impact_report["requested_input"]`（Prepare 时写入）还原
   原始入参——登记事实在业务库，不依赖调用方重传，也不给攻击面。
4. **GATE-014 主机白名单迁入策略包但保留规则侧缺省**：白名单以 `host_whitelist` 进策略包
   （FR-30 / S5 吸收 7），规则文件同时保留 `DEFAULT_HOST_WHITELIST` 作为策略包未提供时的缺省；
   `params_of` 取不到包内参数时返回缺省而非空集——这是「策略可覆盖但不可被清空」的实现口径，
   否则删一个参数就会让规则静默放行。
5. **设计审批的审批人角色取三种**：`versions/approval.py` 接受 `DATA_APPROVER` /
   `SECURITY_APPROVER` / `APPROVER`（PRD 2.1 的 Checker 1 / Checker 2 + 项目审批人），
   而非提示词 §8.4 只列的两种——APPROVER 是 PRD 明确的「设计维度审批」角色。
6. **API-07 不再由 generation 域承载**：S5 吸收 1 要求 API-07 升级为写入与状态迁移接口，
   故端点整体迁至 `versions/router.py`，并**移除** `generation/artifacts.py`
   （避免同一个冻结动作存在两份实现而漂移）。`generation/nodes/gate.py` 改调 versions 域。
7. **PDP 规则集合与策略包的联动**：PDP 的 9 条规则以代码注册表为准，但**策略包中未登记的规则
   不参与评估**（`enabled` 或整条移除即关闭该规则）——这是「策略可热切换」的唯一实现路径；
   若改为「代码里全量跑、包只给参数」，则关规则必须发版。
8. **未登记命令的破坏性判定走关键字兜底且偏保守**：命令名 / 工具名含
   DROP / TRUNCATE / DESTROY / CLEANUP / ROLLBACK / CANCEL 即按破坏性处置（交四眼），
   宁可多问一句也不直接放行。
9. **`.env.example` 补 S6 变量（执行中补做）**：S6 提示词第五节要求「每阶段新增变量时更新」，
   首轮提交遗漏了 4 个 Harness 变量（人类当时直接写进了 `infra/.env`），本轮回填。
10. **S6 未新增 Alembic 迁移**：M6 的 8 张表由 S1 落地，S6 全部读写复用既有 schema；
    `approval_requests` 的 `requested_by / decided_by / reason / decided_at` 在 S1 已建（SPEC 5.2），
    无需变更。DoD-2 的升 / 降 / 升三段因此是既有迁移的自证。


【产出文件清单】
（新增 77 个 + 更新 10 个 + 移除 1 个；首轮提交 80 文件，本轮回填 .env.example 后共 81 个进入提交）

一、src/etl_agent/versions/（M-H 不可变版本与设计审批，新增 9 个）
versions/__init__.py（包入口：M-H 模块边界与覆盖 FR-26/27/28 / AC-04/01/17 的说明）
versions/state.py（版本链 7 态状态机 DRAFT→PROFILING→GENERATED→VALIDATED→WAITING_APPROVAL→APPROVED/REJECTED；TRANSITIONS 白名单；FROZEN_STATUSES / GENERATABLE_STATUSES / PREPARABLE_STATUSES 三组集合与 can_transition / is_frozen）
versions/errors.py（领域错误码：VersionNotFound / VersionStateConflict / ArtifactNotFrozen / ApprovalNotFound / ApprovalSelfForbidden / ApprovalAlreadyDecided——全部复用 SPEC 6.4 既有码，见偏差 1）
versions/service.py（版本生命周期：load_version 按项目隔离、derive_version 派生 parent_version_id、mark_generated、transition、update_with_lock 乐观锁 `WHERE lock_version = ?` + 递增）
versions/artifacts.py（制品 SHA256 冻结：artifact_digests 三类摘要、freeze_version_artifacts 幂等 / 冲突双分支、list_artifacts / artifact_digest_map / require_frozen；正文只取摘要不落库，FR-67）
versions/diff.py（版本 Diff 纯函数集：diff_columns / diff_quality_contract / diff_hocon / diff_artifact_digests / diff_plans / has_changes——不碰 IO，正文由调用方从 Checkpoint 取回）
versions/approval.py（FR-28 设计审批：create_approval_request 绑定 artifact_sha256 + 转 WAITING_APPROVAL（幂等）、decide_approval 三项校验——禁自批 / 职责校验 / 已决策拒绝）
versions/schemas.py（API 模型：PipelineVersionResponse / FreezeRequest / FreezeResponse / DeriveResponse / ApprovalRequestResponse / ApprovalDecisionRequest / ApprovalDecisionResponse / VersionDiffResponse）
versions/router.py（API-07 冻结 + 版本查询 + 派生 + 设计审批 + Diff 五个端点；路径无 project_id 时由资源回溯项目做成员校验）

二、src/etl_agent/harness/（M-J Harness 安全执行内核，新增 29 个）
harness/__init__.py（包入口：M-J 是唯一被授权触发副作用的模块等边界说明）
harness/errors.py（领域错误码：12 个复用 SPEC 6.4 + 3 个新增（ToolNotRegistered / CapabilityInvalid / LeaseHeld），标注 AMEND-005 来源）
harness/service.py（跨子域共享查询：load_preparation_project 反查准备单所属项目，供 API-09/10 鉴权）
harness/schemas.py（API-08/09/10 模型：PreparationCreateRequest / PreparationResponse / DecisionRequest / DecisionResponse / CommitRequest / CommitResponse——响应不回令牌本体）
harness/router.py（API-08 创建准备单 / API-09 四眼审批 / API-10 Commit 三个端点）
harness/lease.py（执行租约：LeaseManager 进程内实现 + acquire / release / get / build_key；S7 可换 DB/Redis 实现，协议不变）
harness/pdp/__init__.py（PDP 子包入口）
harness/pdp/engine.py（PDP 引擎：PdpRequest / RuleOutcome / PolicyDecisionResult / PdpEngine；OUTCOME_RANK 与 RISK_RANK 双维最严聚合；DENY 清空审批要求；规则参数取自签名策略包）
harness/pdp/rules/__init__.py（9 条规则注册表 ALL_RULES + RULE_IDS，顺序固定 P0→P3）
harness/pdp/rules/cross_project.py（HARNESS-P0-CROSS-PROJECT：actor_project_id ≠ project_id → P0 / DENY）
harness/pdp/rules/plaintext_credential.py（HARNESS-P0-PLAINTEXT-CREDENTIAL：与 GATE-001 共用 first_plaintext_credential，只记字段名不记值）
harness/pdp/rules/destructive_op.py（HARNESS-P0-DESTRUCTIVE-OP：注册清单优先 + 关键字保守兜底 → P0 / REQUIRE_APPROVAL（双人））
harness/pdp/rules/secret_exfil.py（HARNESS-P0-SECRET-EXFIL：Secret 外发意图 → P0 / DENY）
harness/pdp/rules/sensitive_export.py（HARNESS-P1-SENSITIVE-EXPORT：RESTRICTED/SECRET 分级 → P1 / REQUIRE_APPROVAL，级别集合来自策略包参数）
harness/pdp/rules/budget_exceed.py（HARNESS-P1-BUDGET-EXCEED：记录 / 字节 / 时长超 FR-45 上限 → P1 / REQUIRE_APPROVAL）
harness/pdp/rules/write_prod.py（HARNESS-P2-WRITE-PROD：命中策略包 environments → P2 / REQUIRE_APPROVAL）
harness/pdp/rules/schema_mismatch.py（HARNESS-P2-SCHEMA-MISMATCH：target_schema_aligned=False → P2 / REQUIRE_APPROVAL）
harness/pdp/rules/routine.py（HARNESS-P3-ROUTINE：兜底恒命中 → P3 / ALLOW）
harness/policy/__init__.py（策略包子包入口）
harness/policy/package.py（签名策略包：build_manifest / build_package / compute_package_sha256 / sign_package / verify_package / load_package / package_from_mapping / package_id；Ed25519 分离签名 + 清单自校验两道把关；DEFAULT_RULE_PARAMS 与 DEFAULT_HOST_WHITELIST）
harness/prepare/__init__.py（Prepare 子包入口）
harness/prepare/service.py（准备单：PrepareInput / PrepareService；compute_input_fingerprint / compute_resource_fingerprint / supervision_contract_digest / resolve_expires_at TTL 夹取；幂等键同载荷返回同一单、不同载荷 409；PDP DENY 不建单；impact_report 含 requested_input 登记事实）
harness/approve/__init__.py（四眼审批子包入口）
harness/approve/service.py（ApproveService.decide：五层校验——状态 / TTL / 槽位范围 / Harness 职责 / 自批，加 (preparation_id,role) 与 (preparation_id,decided_by) 双预检 + IntegrityError 兜底；票数达标转 READY，REJECT 转 REJECTED）
harness/commit/__init__.py（Commit 子包入口）
harness/commit/service.py（CommitService：状态机前置 + 过期检查 + 指纹重验（从 requested_input 还原）+ 审批事实重验 + 签发 Ed25519 单次 Capability + 同事务 ExecutionRun(QUEUED) / HarnessCapabilityGrant(只存 token_sha256) / Outbox(PENDING)）
harness/capability/__init__.py（Capability 子包入口）
harness/capability/issuer.py（CapabilityClaims 十一个绑定字段 + CapabilityIssuer.issue + resolve_ttl（30–900 夹取）+ hash_token）
harness/capability/verifier.py（CapabilityVerifier.verify / verify_against：签名验签 + exp 校验 + claims 绑定逐字段比对）
harness/broker/__init__.py（Tool Broker 子包入口）
harness/broker/tool_broker.py（ToolBroker.dispatch：注册清单校验（非 Agent 自报）→ 准备单状态 → enqueue_command 原子写 Outbox（S6 逻辑出口，不执行副作用）；BrokerCommand / enqueue_command）
harness/broker/replay_guard.py（ReplayGuard.consume：token_sha256 一致性 + consumed_at 预检 + 条件 UPDATE 原子占位，rowcount≠1 即并发重放）
harness/outbox/__init__.py（Outbox 子包入口）
harness/outbox/service.py（事务 Outbox：enqueue / claim_next（条件领取 + 锁）/ mark_sent / mark_failed（retry_count 递增）；S6 只写 PENDING，扫描发送属 S7）
harness/side_effects/__init__.py（副作用子包入口）
harness/side_effects/registry.py（9 类副作用注册表 SIDE_EFFECTS + REGISTRY + ALLOWED_COMMANDS（含主执行命令）+ get / is_registered / is_destructive——Tool Broker 唯一判定来源）

三、配置、脚本与策略包（新增 4 个）
src/etl_agent/config.py（更新：新增 policy_package_path / policy_package_signature_path / harness_signing_key_hex / harness_capability_ttl_seconds / harness_preparation_ttl_seconds 五项 + TTL 边界常量 + harness_public_key_list 属性 + validate_harness() 六项校验（含 prod 强制密钥））
src/etl_agent/main.py（更新：注册 versions / harness 两路由；lifespan 增加 validate_harness()、策略包 load_package 验签（失败即拒绝启动）、PrepareService / CommitService / PdpEngine 装配到 app.state）
src/etl_agent/auth/dependencies.py（更新：新增 require_actor_for_project——资源回溯项目的成员校验，供 API-06/07/09/10 等无 project_id 路径的端点复用）
src/etl_agent/gate/rules/no_plaintext_credentials.py（更新：抽出公共函数 first_plaintext_credential(hocon)，GATE-001 与 PDP HARNESS-P0-PLAINTEXT-CREDENTIAL 共用同一份检测口径，避免两处正则漂移）
src/etl_agent/generation/router.py（更新：移除 API-07 端点（迁至 versions 域），_require_membership 改为复用 auth 公共实现）
src/etl_agent/generation/schemas.py（更新：移除 PipelineVersionFreezeResponse（随端点迁出））
src/etl_agent/generation/service.py（更新：新增 load_candidate_content 从 Checkpoint 取回候选正文（API-07 与 Diff 用）；GENERATABLE_STATUSES 改从 versions.state 导入，取消本地副本）
src/etl_agent/generation/nodes/gate.py（更新：冻结改走 versions 域——先 mark_generated(DRAFT/PROFILING→GENERATED) 再 freeze_version_artifacts(→VALIDATED)，同事务内两步）
src/etl_agent/generation/artifacts.py（移除：冻结实现已由 versions/artifacts.py 接管，避免两份实现漂移，见偏差 6）
.env.example（更新：新增 S6 的 5 个 Harness 变量占位与生成命令注释，见偏差 9）
scripts/gen_policy_package.py（新增：默认策略包生成脚本——清单 + Ed25519 分离签名，生成后自校验摘要可复现；私钥只从环境变量读，不落库不进日志）
configs/credential_placeholders.yaml（新增：S5 吸收 6 的占位符清单——10 个变量（SRC/SINK_HOST/PORT/DB/USER/PASSWORD、FILE_ASSET_BASE）+ 治理约束（GATE-001 放行占位符、S7 物化变量名契约、禁止事项））
configs/policy/harness-v2-20260808.json（新增：交付的策略包清单——9 条规则（rule_id/version/enabled/params）+ host_whitelist 5 项 + package_sha256）
configs/policy/harness-v2-20260808.sig（新增：上述清单的 Ed25519 分离签名，hex）

四、tests（新增 17 个 + 更新 2 个）
tests/versions/__init__.py
tests/versions/conftest.py（design_approvers 夹具：给 admin-b 补 A 项目成员关系 + SECURITY_APPROVER，满足「审批人 ≠ 申请人 + 持职责」）
tests/versions/test_state_machine.py（6 个用例：全链迁移可达 / 跳跃迁移拒绝 / 禁原地覆盖 / 冻结幂等与冲突）
tests/versions/test_derivation.py（3 个：parent_version_id 正确 / 终态版本仍可派生 / 跨项目派生 404 且零派生行）
tests/versions/test_optimistic_lock.py（2 个：过期 lock_version 更新被拒且不污染现场 / 字段更新递增）
tests/versions/test_artifacts.py（4 个：三类摘要各为其值且版本级取 HOCON / quality_contract 缺省取 plan 内 / 未冻结拒绝 / 冻结后状态与摘要）
tests/versions/test_diff.py（4 个纯函数：Schema 增删改 / 质量规则增改 / HOCON 行集差 / 摘要级差异与非预期形态）
tests/versions/test_router.py（8 个：API-07 冻结 / 未冻结 409 / 未知 404 / 派生+审批 / 自批 409 / 无职责 403 / Diff / 跨项目 403）
tests/harness/__init__.py
tests/harness/conftest.py（harness_package / harness_public_keys / harness_signing_key / harness_app / tampered_package_path 五个夹具 + autouse 的 harness_roles——AC-01 四人职责分配，含 admin-b 与 outsider 的 A 项目成员关系补齐）
tests/harness/test_pdp.py（13 个：9 条规则全注册 / 聚合优先级 / 跨项目 DENY / 明文凭据 DENY 且不泄漏值 / 占位符放行 / DENY 清空审批 / 破坏性双人 / 敏感外发安全审批 / 预算超限证据 / 生产写入与 Schema 漂移 / 常规 ALLOW / Secret 外发 DENY / 跨项目压过敏感外发 / 策略包可关规则 / 未登记命令保守判定）
tests/harness/test_policy_package.py（8 个：验签通过 / 清单篡改拒绝 / 签名篡改拒绝 / 错公钥拒绝 / 无公钥拒绝 / 文件缺失拒绝 / rule_id+version 唯一 / 摘要不含自身与签名回环）
tests/harness/test_prepare.py（6 个：指纹稳定 / 幂等同键 / 幂等冲突 409 / PDP DENY 无准备单 / 未冻结 409 / 风险槽位分配（P3 无槽 vs P0 双槽）/ environment 枚举 422）
tests/harness/test_four_eyes.py（AC-01 核心，8 个：自批 / 职责槽被占 / 一人一槽 / P0 需两人且分批验证 READY / 无职责 403 / evidence_sha256 落库 / REJECT 阻断 / DB 双唯一 IntegrityError）
tests/harness/test_commit.py（AC-05 核心，6 个：同事务 ExecutionRun+Outbox / 签发 Capability claims 绑定 / 审批不足 / 指纹篡改拒绝 / 重复提交 / 非 OPERATOR 403 / 跨项目 403）
tests/harness/test_capability.py（7 个：claims 绑定字段齐全 / TTL 夹取与 ≤900 / 验签与令牌篡改 / 过期 401 / 绑定漂移 / 只存摘要 / 私钥非法拒绝）
tests/harness/test_replay_guard.py（7 个：二次消费 REPLAYED / 未知与摘要不匹配 / 过期令牌 / 未注册副作用 / 注册清单内只写 Outbox 不消费令牌 / Commit 后 PENDING 行 / 零明文响应；另覆盖 Outbox 状态机 PENDING→SENDING→SENT→FAILED）
tests/harness/test_side_effects.py（3 个：9 类全注册 / 自报副作用被拒 / 破坏性以清单判定）
tests/harness/test_lease.py（3 个：并发单租约 / 过期可重取 / 租约键按类型隔离）
tests/conftest.py（更新：S6_TEST_PLAN / S6_TEST_HOCON 常量 + frozen_version 夹具（mark_generated + freeze_version_artifacts 走真实冻结路径）；app fixture 经 attach_harness_services 注入 PDP/Prepare/Commit）
tests/generation/test_state_machine.py（更新：冻结产物由 {plan, hocon} 收敛为 SPEC 5.2 的三类 {plan, hocon, quality_contract}）


【DoD 逐项结果】
- [x] DoD-1  产出文件齐全 —— 证据：第七节目录结构 23 个路径逐项存在性校验，缺失 0 个；
       本报告「产出文件清单」四节共 81 个文件逐条列出（含用途）。
- [x] DoD-2  Alembic 可升可降 —— 证据：`upgrade head` → `downgrade base`（3bef75172b14 → 空）
       → `upgrade head`（→ 3bef75172b14）三段全部 exit 0；结束后 `\dt` 复核 M6 的 8 张表齐备。
       S6 未新增迁移（见偏差 10）。
- [x] DoD-3  版本链测试通过 —— 证据：`pytest tests/versions -q` → **29 passed**。
- [x] DoD-4  Harness 测试通过 —— 证据：`pytest tests/harness -q` → **64 passed**。
- [x] DoD-5  回归测试通过 —— 证据（分目录执行，与 S5 各目录计数逐项一致）：
       infrastructure 25 passed / auth 17 passed / projects 17 passed / connections 18 passed
       / secrets 28 passed, 1 skipped / profiles 31 passed / files 25 passed
       / generation 18 passed, 1 skipped / gate 27 passed；
       全量：`pytest tests -q` → **299 passed, 2 skipped**（S5 基线 206 passed, 2 skipped + S6 新增 93）。
       2 个 skip 均为 S5 既有（无真实 LLM API Key 的 provider 跳过项），与基线一致。
- [x] DoD-6  AC-01 四眼职责分离 —— 证据：见下方「AC-01 逐条验证」。
- [x] DoD-7  AC-05 prepare→approve→commit —— 证据：见下方「AC-05 逐条验证」。
- [x] DoD-8  AC-05 防重放 —— 证据：`test_capability_replay_guard`：同令牌第二次消费 →
       **HARNESS_409_CAPABILITY_REPLAYED**；`test_replay_guard_rejects_unknown_token`：
       未知令牌与 token_sha256 不匹配同样拒绝（不泄露令牌存在性）。
- [x] DoD-9  PDP 9 条规则 —— 证据：`test_pdp_9_rules`（RULE_IDS 与 SPEC 8.2 清单逐字一致）
       + `test_pdp_aggregation_priority`（DENY>DEFER>REQUIRE_APPROVAL>ALLOW、P0>P1>P2>P3
       两级排序 + RISK_APPROVALS 槽位表）+ `test_pdp_deny_no_approval`（DENY 清空审批要求）。
- [x] DoD-10 PDP DENY 无放行通道 —— 证据：`test_prepare_deny_returns_403`：
       跨项目意图 → PolicyDeny（HARNESS_403_POLICY_DENY），`harness_policy_decisions` 落 1 行
       DENY 决策（留证），`execution_preparations` **0 行**（无准备单可批）。
- [x] DoD-11 签名策略包 —— 证据：`test_policy_package_verify`（9 规则 + 5 白名单 + 验签通过）
       / `test_policy_package_tamper_manifest`（改清单内容 → 摘要不符拒绝）
       / `test_policy_package_tamper_signature`（改签名 → 验签失败）/ 错公钥 / 无公钥 / 文件缺失
       / rule_id+version 重复 —— 7 类失败全部拒绝加载（启动即失败，不降级放行）。
- [x] DoD-12 Tool Broker 唯一出口 —— 证据：`test_tool_broker_registry_only`：
       Agent 自报的 `TELEPORT_DATA` → **HARNESS_403_TOOL_NOT_REGISTERED**；
       `test_side_effects_9_registered`：9 类副作用 + 主执行命令共 10 个可下发命令。
- [x] DoD-13 事务 Outbox —— 证据：`test_outbox_pending_written`（Commit 后恰 1 条 PENDING，
       command_type=PIPELINE_EXECUTE）+ `test_outbox_state_machine`
       （PENDING→SENDING→SENT→FAILED 与 retry_count）+ `test_outbox_second_claim_returns_none`
       （条件领取保证同一命令只被一个扫描器领走）。
- [x] DoD-14 执行租约 —— 证据：`test_lease_mutex`（并发第二次 → HARNESS_409_LEASE_HELD；
       同 owner 可重入、释放后他人可获取）/ `test_lease_expiry_allows_reacquire`
       / `test_lease_key_isolated_by_preparation_and_type`。
- [x] DoD-15 版本链 —— 证据：`test_no_overwrite_frozen`（已冻结版本 mark_generated 与
       异内容 freeze 均 409，原摘要不变）/ `test_derivation_parent_id` / `test_optimistic_lock`。
- [x] DoD-16 AC-02 凭据零明文 —— 证据：`test_zero_plaintext_harness_responses`（Prepare 响应
       不含需求正文与连接密码）；验收命令 7 的正则扫描命中 9 处，全部为 `${}` 占位符
       （src 2 处：mock_provider / conftest 夹具）或测试构造的假值（`s3cr3t-value` 是 GATE-001
       故意失败样本、`irrelevant-but-valid-length` 是 S3 既有的连通性测试值），
       **src/ 下无任何真实凭据字面量**；`infra/.env` 未被跟踪也未被扫描命中。
- [x] DoD-17 Lint / Type 干净 —— 证据：`uv run ruff check src/ tests/ scripts/` → **All checks passed!**；
       `uv run mypy src/etl_agent/versions/ src/etl_agent/harness/ src/etl_agent/config.py
       src/etl_agent/main.py` → **Success: no issues found in 46 source files**。
- [x] DoD-18 未修改基线 —— 证据：`git show --name-only 0d052f1` 与 `b34b41b` 中均无
       `*-Baseline` 文件（0 命中），无 `docs/prd/`、`docs/spec/` 下既有文件被改。
- [x] DoD-19 代码注释合规 —— 证据：抽检脚本对 `src/etl_agent/**/*.py`（排除 `__init__.py`，
       共 130 个）校验文件头三层（SPEC 依据 / 覆盖 FR / 对应 AC），输出 **0 个 MISSING HEADER**；
       模块级 docstring 均写明口径与边界（如 DENY 不放行、S6 不执行副作用、正文不落业务库）。
- [x] DoD-20 上下文读取确认 + 遗留分类归档 + .env 未被修改 —— 证据：开头 10 个文件全部「已读」；
       「S7 吸收清单」8 条 + 「AMEND-005 草稿」（类型 C）已归档；`infra/.env` 未写入
       （`git status` 无该文件，4 个 Harness 变量仍为人类原值）。

【AC-01 逐条验证】
验收条件（PRD）：给定项目管理员已登录，当为三名独立成员分别配置 DATA_ENGINEER /
DATA_APPROVER / SECURITY_APPROVER 职责时，则系统强制拦截申请人自批与职责混用
（(preparation_id, decided_by) 与 (preparation_id, role) 双唯一约束 + API 双重校验）。

| 场景 | 期望 | 实际 |
|------|------|------|
| 申请人自批 | 409 HARNESS_409_SELF_APPROVAL | `test_four_eyes_self_approval`：engineer-a 提交 P0 准备单后自己审批 → 409 `HARNESS_409_SELF_APPROVAL`，决策表 0 行 ✅ |
| 同角色二次审批 | 409 HARNESS_409_ROLE_TAKEN | `test_four_eyes_role_taken`：admin-a 占 DATA_APPROVER 槽后，同为非申请人的 outsider 再占同槽 → 409 `HARNESS_409_ROLE_TAKEN` ✅ |
| 一人占两槽 | 409 HARNESS_409_SELF_APPROVAL | `test_four_eyes_one_person_one_slot`：admin-b 先占 SECURITY_APPROVER 再占 DATA_APPROVER → 409 `HARNESS_409_SELF_APPROVAL`（PRD 2.3-1 一人一槽）✅ |
| P0 双人批准 | required_approvals=2，一人不足 | `test_four_eyes_p0_requires_two`：P0（CANCEL_EXECUTION）required_approvals=2、required_roles=[DATA_APPROVER, SECURITY_APPROVER]；admin-a 单票后 status 仍 WAITING_APPROVAL；admin-b 第二票后 → READY ✅ |
| 无 Harness 职责 | 403 AUTH_403_ROLE_REQUIRED | `test_four_eyes_role_required`：admin-a（只持 DATA_APPROVER）去批 SECURITY_APPROVER 槽 → 403 `AUTH_403_ROLE_REQUIRED`（ADMIN 不隐式升级，FR-03）✅ |
| 决策证据留痕 | evidence_sha256 落库 | `test_approval_evidence_sha256`：64-hex 证据与理由完整落库并可查 ✅ |
| REJECT 阻断 | 后续审批被拒 | `test_four_eyes_reject_blocks_progress`：REJECT 后准备单转 REJECTED，他人再批 → 409 `BIZ_409_STATE_CONFLICT` ✅ |
| DB 双唯一兜底 | IntegrityError | `test_db_double_unique_constraint`：绕过 API 直插同 (preparation_id, role) 与同 (preparation_id, decided_by) 两行 → 均抛 `IntegrityError`，证明约束在 DB 层而非仅应用层 ✅ |

【AC-05 逐条验证】
验收条件（PRD）：给定已冻结版本，当完整执行 prepare→approve→commit 时，则生成含影响报告与
回滚计划的准备单、完成四眼审批（required_approvals=2 场景双人均批）、签发 Ed25519 单次
Capability（TTL ≤ 300s），且第二次使用同一令牌被 Replay Guard 拒绝。

| 场景 | 期望 | 实际 |
|------|------|------|
| 准备单含影响报告 + 回滚计划 | 非空 impact_report / rollback_plan | `test_prepare_*` 系列 + `test_commit_atomic_outbox`：impact_report 含 effects / bounded / resource_count / source_sink_profiles / artifact_digests / requested_input，rollback_plan 含影子表 Swap 策略 + 7 天保留期 ✅ |
| 四眼审批 | 双人均批才 READY | `_approve_p0`（DATA_APPROVER + SECURITY_APPROVER 两票）后 status=READY；单票 → `test_commit_insufficient_approval` 409 `HARNESS_409_APPROVAL_INSUFFICIENT` ✅ |
| 签发单次 Capability | Ed25519，TTL ≤300s | `test_commit_signs_capability`：claims 绑定 tool_name / tool_version / subject / environment / artifact_sha256 / supervision_contract_sha256 / project_id / preparation_id / pipeline_version_id / allowed_mitigations 共 10 项；`test_capability_ttl_bounded`：默认 300s、夹取区间 30–900（SPEC 6.2 上限 900）✅ |
| 同事务原子性 | ExecutionRun + Outbox | `test_commit_atomic_outbox`：ExecutionRun(QUEUED, MANUAL, harness_preparation_id) + Outbox(PENDING, PIPELINE_EXECUTE) + Grant(token_sha256 非空) + 准备单 COMMITTED 四者同事务 ✅ |
| 指纹服务端重验 | 篡改即拒绝 | `test_commit_reverify_fingerprint`：篡改 input_fingerprint 后 Commit → 409 `BIZ_409_STATE_CONFLICT` ✅ |
| 防重放 | 二次消费拒绝 | `test_capability_replay_guard`：consume 一次成功（consumed_at 落库），第二次 → 409 `HARNESS_409_CAPABILITY_REPLAYED` ✅ |
| 令牌过期 | 401 | `test_capability_expired`：构造已过期令牌 → 401 `HARNESS_401_CAPABILITY_EXPIRED` ✅ |
| 重复提交 / 非 Operator / 跨项目 | 409 / 403 / 403 | `test_commit_already_committed`（409 `HARNESS_409_ALREADY_COMMITTED`）/ `test_commit_only_operator`（403 `AUTH_403_ROLE_REQUIRED`）/ `test_commit_cross_project_403`（403）✅ |
| 唯一副作用出口 | 非注册命令拒绝 | `test_tool_broker_registry_only`：`TELEPORT_DATA` → 403 `HARNESS_403_TOOL_NOT_REGISTERED`（FR-34：以注册清单判定，非 Agent 自报）✅ |
| S6 不执行副作用 | 只发令到 Outbox | `test_tool_broker_enqueue_only`：注册清单内副作用 dispatch 后仅落 Outbox PENDING 行，令牌 **未被消费**（消费点在 S7 Worker，禁止事项 23）✅ |


【S7 吸收清单】
1. Outbox 扫描 / 发送 / 重试调度：`harness/outbox/service.py` 的 `claim_next`（`status=PENDING
   AND (locked_until IS NULL OR locked_until < now)` 条件领取）/ `mark_sent` / `mark_failed`
   已就绪，S7 接 Celery Beat 周期任务（SPEC 8：扫描 2s、指数退避重试、`acks_late` +
   `prefetch_multiplier=1` + `reject_on_worker_lost` 的至少一次投递承诺）。
2. Replay Guard 的真实调用点在 Worker：`broker/replay_guard.py::consume` 与
   `broker/tool_broker.py::dispatch` 已就绪（S6 已测），S7 在拉取 Outbox 后先
   `CapabilityVerifier` 验签再消费令牌，最后才调 SeaTunnel。
3. 执行租约的持久化实现：`harness/lease.py` 为进程内字典 + TTL 实现，`LeaseManager`
   协议（acquire / release / get / build_key）保持不变，S7 可换 DB / Redis 实现。
4. 真实引擎检查：`gate/engine_check.py::EngineChecker` 协议已就位，S7 在 Worker 侧物化
   运行期变量后调 SeaTunnel `--check`，只回传脱敏摘要（FR-20 / S5 吸收 5）。
5. 占位符变量名契约：`configs/credential_placeholders.yaml` 与 MockProvider 产出必须与
   S7 `credential_materialization` 的注入变量名一致（SRC_PASSWORD / SINK_HOST 等 10 个），
   不一致会导致运行期物化失败。
6. `harness_policy_decisions.rule_evidence` 目前只写规则 ID / 风险 / 脱敏命中事实；S7 若做
   证据账本（FR-36 哈希链）需与本表的 `policy_package_sha256` 串联以保证策略可追溯。
7. 监督契约预算目前是 `DEFAULT_SUPERVISION_CONTRACT` 常量（FR-45 七项）；S8 监督引擎应按
   `preparation.supervision_contract` 快照执行，并把摘要写进
   `ExecutionRun.supervision_contract_sha256`（Commit 已写入）。
8. API-11（触发受管执行）/ API-12（查询执行）/ API-13（受管回滚申请）未实现：
   S7 起由 ExecutionRun 状态机接管，API-13 本质是副作用准备单的创建入口（走
   `HarnessSideEffectPreparation` 同四表结构）。

【AMEND-005 草稿】
（类型 C：新增 3 个 HARNESS_ 错误码；状态 Draft，不自动提交，S6 禁止事项 22）
文件路径：docs/spec/amendments/AMEND-005-harness-error-codes.md（未入库，待人类审核）
新增：
| 段 | HTTP | 错误码 | 含义 | 定义位置 |
|----|------|--------|------|---------|
| HARNESS_ | 403 | `HARNESS_403_TOOL_NOT_REGISTERED` | 命令 / 副作用不在注册清单（实现或配置错误，非策略拒绝） | `harness/errors.py::ToolNotRegistered` |
| HARNESS_ | 401 | `HARNESS_401_CAPABILITY_INVALID` | 令牌签名验签失败 / 哈希不匹配 / claims 绑定漂移 | `harness/errors.py::CapabilityInvalid` |
| HARNESS_ | 409 | `HARNESS_409_LEASE_HELD` | 执行租约已被占用（并发重入） | `harness/errors.py::LeaseHeld` |
复用（不新增，共 13 个）：`BIZ_409_STATE_CONFLICT`、`BIZ_404_NOT_FOUND`、
`BIZ_409_IDEMPOTENCY_CONFLICT`、`BIZ_410_PREPARATION_EXPIRED`、`HARNESS_403_POLICY_DENY`、
`HARNESS_409_SELF_APPROVAL`、`HARNESS_409_ROLE_TAKEN`、`HARNESS_409_ALREADY_COMMITTED`、
`HARNESS_409_APPROVAL_INSUFFICIENT`、`HARNESS_409_CAPABILITY_REPLAYED`、
`HARNESS_401_CAPABILITY_EXPIRED`、`AUTH_403_ROLE_REQUIRED`、`AUTH_403_NOT_PROJECT_MEMBER`。
草稿另列启动期失败三类（ProductionConfigError / PolicyPackageInvalid /
CapabilityIssuanceError，均为拒绝启动而非 API 错误码）与 3 个待人类裁决问题
（401 段位选择、LEASE_HELD 是否独立设码、版本域统一复用是否接受）。
审核动作：人类确认后 `git add docs/spec/amendments/AMEND-005-harness-error-codes.md`
+ `git commit`，状态改为 Accepted。

【git_ship 结果】
- branch: feat/S6-harness（上游 stage-S5-done，起点 393103b）
- commit-1: 0d052f166cd481ca3e0af24418a681229e2b8804
  `feat(S6): 版本与 Harness 安全执行内核 [SPEC v1.0.3-Baseline + AMEND-001/002/003/004]`
  （80 files changed, +7039 / −181）
- commit-2: b34b41b `docs(S6): 阶段报告 [SPEC v1.0.3-Baseline]`（本报告首版，165 行）
- tag: stage-S6-done（annotated，已推送）
- push: 成功（origin feat/S6-harness 393103b..0d052f1、0d052f1..b34b41b；origin stage-S6-done 新建）
- 未入库（按提示词要求）：AMEND-005 草稿

【github_ops 结果】
- PR URL: https://github.com/vr337/etl_agent/pull/6
- 标题：[S6] 版本与 Harness 安全执行内核
- 标签（4 个，均已确认存在）：stage:S6 / spec:v1.0.3-Baseline / ac:AC-01 / ac:AC-05
- 里程碑：etl_agent v1.0 实施（描述已追加 S6 完成记录：T-11 / T-12，指向 PR #6）
- milestone 更新: 成功
- auto_pr: false（未自动创建第二个 PR）

【遗留问题】
1. AMEND-005 草稿待人类审核入库（阻塞 S7 正式基线合并：否；S7 可先按草稿临时使用）。
2. `.env.example` 补写动作滞后（偏差 9）：首轮提交只覆盖了 config / main / 路由，遗漏示例文件；
   本轮已补 5 个变量占位。若人类已按真实值配置 `infra/.env`，示例文件仅作文档用途，无功能影响。
3. 测试夹具对 `harness_roles` 采用 autouse：任何 `tests/harness/` 下的用例都会拿到四人职责，
   新增用例若意图验证「无职责被拒」需自行降级（S7 写 harness 新用例时注意）。
4. `PdpEngine` 目前只在 lifespan 与测试夹具中构造；S7 Worker 侧若需复用 PDP，
   应从 `app.state.pdp_package` 走同一份验签后实例，不可重新加载磁盘包（保证策略版本一致）。
5. 其余无。

【下一阶段建议】
建议启动 S7（M-K/M-L 运行时调度与质量 / 监督）——对应 AC-06/07/08。S6 无阻塞项：
Outbox / Replay Guard / 租约 / 引擎检查四类接口已就位，S7 只需接 Worker 与 Beat；
请优先按「S7 吸收清单」第 1~5 条确认调度间隔与占位符物化契约后再编码。

=== 阶段报告结束 ===
