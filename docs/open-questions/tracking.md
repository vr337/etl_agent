# etl_agent 待确认项 + 遗留问题追踪表

> 数据来源：PRD Q1~Q11 + SPEC S-Q1~S-Q8 + 各阶段报告遗留
> 更新时机：每个阶段完成后由 WorkBuddy 生成草稿，人类审核提交

## 一、基线待确认项（PRD Q + SPEC S-Q）

| 编号 | 来源 | 问题摘要 | 处理时点 | 状态 |
|------|------|---------|---------|------|
| Q1/S-Q1 | PRD/SPEC | 生产可信网关归属 | 上线前 | Open |
| Q3 | PRD | LLM Provider 选型 | S10 前 | Open |
| Q4/S-Q4 | PRD/SPEC | SeaTunnel 部署形态 | S10 后 | Open |
| Q5/S-Q7 | PRD/SPEC | Vault 部署与 KMS | 上线前 | Open |
| Q6 | PRD | Benchmark 基线节奏 | S10 前 | Open |
| Q7 | PRD | 多环境配置分离 | 上线前 | Open |
| Q8/S-Q6 | PRD/SPEC | ClickHouse/S3/REST 是否本期 | S10 后 | Open |
| Q10 | PRD | 多租户隔离级别 | 上线前 | Open |
| Q11/S-Q3 | PRD/SPEC | Celery Beat 部署形态 | 上线前 | Open |
| S-Q5 | SPEC | 是否 K8s 部署 | 上线前 | Open |
| S-Q8 | SPEC | CI/CD 是否预装 uv | 建 CI 时 | Open |

## 二、阶段遗留（跨阶段遗留事项）

| 阶段 | 遗留 # | 摘要 | 分类 | 处理 |
|------|-------|------|------|------|
| S7 | 1 | SeaTunnel REST `submit-job` NPE | CLI 通道已落地（部分完成） | **T-ENGINE-01**（AC-16 闭环待 T-ENGINE-02） |
| S10 | 7 | 生成模板硬编码 PG 驱动 +  worker 容器内寻址不可达 | 独立排期 | **T-ENGINE-02** |
| S7 | 6 | 调度环境继承口径 | 本阶段可收尾 | ✅ 人类裁决 A |
| S10 | 1 | demo seed 脏行 3 不可落库（address 超宽） | 演示数据形态待裁决 | **T-DEMO-01** |
| S10 | 2 | 非演示路径 12 处路由漏 commit | 独立排期 | **T-COMMIT-01** |
| S10 | 3 | 审计钩子未接线，证据账本恒空 | 已修复（F3） | ✅ 人类裁决已闭环 |
| S10 | 4 | 影子表执行通道为 Logging，不建真实 Doris 表 | 独立排期 | **T-SHADOW-01** |
| S10 | 5 | 质量契约 valid_sink 与派生影子表名漂移 | 独立排期 | **T-SHADOW-02** |
| S10 | 6 | 控制台刷新后显示态未持久化（actorRole 空；name 本 PR 已修） | 独立排期 | **T-CONSOLE-01** |

## 三、独立运维事项

### T-ENGINE-01：SeaTunnel REST submit-job NPE（跨 4 版本）—— **部分完成**

- **状态**：**部分完成**（2026-10-16，fix/T-ENGINE-01）
- **发现**：S7 阶段（2026-10-15）
- **根因**：SeaTunnel 2.3.x REST 提交路径 `ConfigShadeUtils.processConfig` NPE
- **已试（4 版本均失败）**：
  - flowpilot/seatunnel:2.3.13 → NPE at `ConfigShadeUtils.java:184`
  - apache/seatunnel:2.3.13 → 同一位置 NPE
  - apache/seatunnel:2.3.11 → NPE at `ConfigShadeUtils.java:181`
  - apache/seatunnel:2.3.8 → `curl: (52) Empty reply from server`
- **已排除**：
  - ❌ 请求体问题（JSON + HOCON 两种格式都失败）
  - ❌ 镜像问题（flowpilot + apache 官方都失败）
  - ❌ 版本问题（2.3.8 / 2.3.11 / 2.3.13 三个版本都失败）
- **已完成（本 PR，CLI 提交通道落地）**：
  - ✅ 引擎实测：`seatunnel.sh --config <f> --async` 提交本机集群成功（job id 可解析，
    ~2s 返回）；`--check` 校验可用；`--cancel-job` 取消可用；REST `/job-info/{id}`
    对 CLI 提交的作业有效
  - ✅ `CliSeaTunnelClient`（check/submit/stop 走 CLI、job_status 复用 REST）+ `engine_mode=real-cli`
  - ✅ `infra/Dockerfile.worker`（worker 专用镜像：JDK8 同源 + SeaTunnel 引擎客户端层裁剪
    + connector 白名单 + cluster-members=seatunnel:5801），镜像 2.71GB，启动日志/容器 env 双证据
  - ✅ 14 个新测试 + 904 passed 全量绿
- **仍未闭环**：**AC-16「Doris 有真实数据」未达成**——真引擎首次执行暴露**生成模板缺陷**
  （详见 **T-ENGINE-02**），本 PR 按 X3 范围控制不修；T-SHADOW-01（影子表通道）在真实链路
  下同样仍未实现（当前 HOCON 为裸 `insert into order_wide` 直插目标表，非原子 Swap 语义）
- **关联**：SPEC S-Q4（SeaTunnel 部署形态）；T-ENGINE-02（生成模板，阻塞 AC-16 闭环）
- **当前镜像**：`flowpilot/seatunnel:2.3.13`（compose e2e/exec）

### T-ENGINE-02：生成模板不感知 connector_type + 容器内寻址不可达（新，Open）

- **状态**：Open（2026-10-16，T-ENGINE-01 真引擎首跑暴露；独立 PR 立项）
- **发现**：T-ENGINE-01 步骤 4 前置判定（生成 HOCON 后读 checkpoint 候选正文）
- **缺陷 1（驱动/协议硬编码）**：HOCON 模板 source/sink 均硬编码
  `driver = "org.postgresql.Driver"` + `jdbc:postgresql://`，**不按连接的
  `connector_type`（MYSQL / DORIS）渲染**。物化后 source 指向 MySQL 容器
  （MySQL 协议）、sink 指向 Doris FE（MySQL 协议）→ PostgreSQL 驱动连 MySQL
  协议端口，JDBC 握手即败。mock 时代引擎不出水故完全不可见。
- **缺陷 2（执行视角寻址）**：物化取连接行 host/port **原值**（演示数据按宿主机视角填
  `127.0.0.1:13306/9030`），而引擎提交发生在 **worker 容器内**——`127.0.0.1` 是它自己，
  source/sink 均不可达。需要「执行视角 host 解析」（容器网络名映射或连接行执行字段）。
- **附带（次要）**：`insert into order_wide` 裸 INSERT 无列清单，列序依赖 JDBC 默认排序。
- **影响**：**AC-16 不闭环**——真引擎模式下所有作业必然失败（两个缺陷各自都可致命）；
  T-ENGINE-01 的 CLI 提交通道已就绪，此为其下游唯一阻断。
- **修复范围**：生成域（HOCON 模板渲染按 connector_type 选 driver/URL + 执行视角
  寻址解析）；改动涉及契约面，可能需 AMEND 登记；引擎侧驱动不缺
  （lib 已有 postgresql-42.7.5 / mysql-connector-j-8.4.0，worker 白名单含 connector-jdbc）。
- **关联**：T-ENGINE-01（上游已就绪）；T-SHADOW-01（影子表通道，真实链路另一缺口）；
  T-SHADOW-02（valid_sink 契约漂移，修复时一并评估）

### T-DEMO-01：demo seed 脏行 3 不可落库（address 超列宽）

- **状态**：Open
- **发现**：S10 场景 A 演示准备（2026-10-16，MySQL 重新 init 时暴露）
- **现象**：`infra/e2e/mysql-init/01-seed.sql` 脏数据 3（order_id=9003，
  `REPEAT('X', 400)` 地址）超出源表 `address VARCHAR(255)`，MySQL 8 严格模式
  报 `ERROR 1406 (22001) Data too long for column 'address'` 并中止执行——
  **容器首次 init 实际也只落 1002 行**（缺 9003），且后续
  `CREATE USER demo_reader` 从未执行（当年依赖人工补登）。
- **根因**：源表列宽设计缺口——原意图是「源端允许超长地址、由目标端
  `order_wide.address VARCHAR(255)` 的长度约束拒绝」，但源表自身先拒。
- **影响**：质量分流的「地址超长」错误码不会出现（当前 2 条脏行：
  NULL 金额、未来水位）；不影响 AC-10 主链路其余证据。
- **修复建议**（未实施，待人类裁决）：源表 `address` 加宽至 VARCHAR(500)
  （或 TEXT），保留 400 字符脏行。注意会改变演示数据形态
  （1002 → 1003 行、错误码分布 +1），需与质量报告预期一并评估。
- **关联**：S10 T-22 demo-scenario-a；`etl_quality_summary` 表注释

### T-COMMIT-01：非演示路径 12 处路由漏 commit

- **状态**：Open（独立排期，不混入 fix/S10-demo-script PR）
- **发现**：S10 场景 A 联调排障（2026-10-16，F1 全库 AST 扫描：40 个写路由中
  14 个无 commit，generation create/resume 经 service 内部 commit 判否后实缺 15，
  演示路径 4 已修，余 12 在此登记）
- **现象**：`get_session` 退出即回滚未提交事务，以下路由只 flush 不 commit，
  「返回 2xx 但数据丢失」：
  - `policy`：PATCH update_policy（1）
  - `secrets` rotations ×5（申请 / 审批 / 执行等旋转动作）
  - `migrations` ×3、`destructions` ×2
  - `files` upload_file（1）
- **影响**：对应功能在真实调用下静默丢数据；单测抓不到——conftest 的
  savepoint 模式下应用侧 commit 只释放 savepoint，与 F1 同族盲区
- **修复建议**（未实施，待排期）：照 F1 范式补 `await session.commit()` 并配
  commit-spy 回归测试（`tests/test_commit_persistence.py` 同款探针）
- **关联**：fix/S10-demo-script（F1 演示路径 4 处已修，人类裁决范围控制不外溢）

### T-AUDIT-01：审计钩子未接线，证据账本恒空（已修复）

- **状态**：✅ 已修复（F3，人类裁决闭环；fix/S10-demo-script）
- **发现**：S10 场景 A 修复后重跑（2026-10-16，STEP 12 evidence verify 404 判定）
- **现象**：`audit/hooks.py` 的 7 类 `on_*` 钩子（PDP 决策 / Prepare 创建 /
  审批决策 / Commit 签发 / 执行迁移 / 监督动作 / 回滚）在 `src/` 全域
  **零调用点**（死代码，仅 `tests/audit` 直调）→ `harness_evidence_streams` /
  `harness_evidence_events` 恒 0 行 → `GET /projects/{id}/evidence-stream/verify`
  返回 404 `BIZ_404_EVIDENCE_STREAM_NOT_FOUND`
- **根因**：S9 实现了钩子与证据链模块，但未在 preparation / commit / execution /
  PDP 服务路径接线；测试直调钩子故全绿（又一联调盲区）
- **修复**（fix/S10-demo-script，七钩子全部接线；钩子内只 flush，证据随调用方
  既有事务提交——与 T-COMMIT-01 同族风险同解）：
  - prepare service：`on_pdp_decision` + `on_prepare_created`；**DENY 特判**：
    raise PolicyDeny 前 service 单独 commit 决策行 + 证据（403 回滚不冲掉 P0 留痕）
  - approve service：`on_approval_decision`；commit service：`on_commit_issued`
  - execution service：`on_execution_transition` 接在 `transition` 咽喉
    （project_id 经派生链单查询，孤儿运行告警降级不抛）
  - supervise 任务：`on_supervision_action`；rollback service 三相：
    `on_rollback_event`（REQUESTED / DECIDED / EXECUTED）
  - 测试：tests/harness|worker|rollback 三个 test_audit_hooks.py 共 6 例
    （含 DENY 证据存活、happy path 证据行）
- **遗留观察**：`execution/router.py` 详情端 `supervision_snapshot` 硬编码 None
  （S7 遗留）仍未回填；检查点签名器（CheckpointSigner）生产未装配——
  证据链哈希完整但无 Ed25519 检查点，另行评估
- **关联**：fix/S10-demo-script（F3）；docs/spec/amendments/AMEND-008

### T-SHADOW-01：影子表执行通道为 Logging，不建真实 Doris 表

- **状态**：Open（独立排期）
- **发现**：S10 场景 A 修复后重跑（2026-10-16，execution.shadow_ready 事件只有日志、
  Doris 侧 `order_wide_shadow` 无任何 worker 侧 DDL 痕迹）
- **现象**：`consume_outbox.scan_outbox_once` 的 `shadow_executor` 参数缺省为
  **Logging 通道**（无 Doris 的开发/测试环境用），生产 `_run()` 未注入真实
  Doris 影子表执行器 → 影子表只落事件日志，不在 Doris 侧真实创建
- **影响**：原子 Swap（FR-39）缺前置影子表；质量分流「先写 shadow 再 REPLACE」
  的演示链路在 mock 引擎下本就不搬数据，与 T-ENGINE-01 叠加后影子表链路整体
  未闭环。当前不影响 AC-10 主链路其余证据（mock 口径下 Doris 表保持空表）
- **修复建议**（未实施，待排期）：实现 Doris 影子表执行器（`CREATE TABLE ... LIKE`
  已由 init SQL 提供示例），`_run()` 按环境注入；补 spy 测试断言 shadow DDL 真实下发
- **关联**：T-ENGINE-01；F5 mock 引擎接线（fix/S10-demo-script e0632da）

### T-SHADOW-02：质量契约 valid_sink 与派生影子表名漂移

- **状态**：Open（独立排期）
- **发现**：S10 场景 A 修复后重跑（2026-10-16，事件流 `execution.shadow_contract_drift`）
- **现象**：生成计划 `quality_contract.valid_sink` 与按计划推得的影子表名
  （`shadow_table_name(valid_sink)`）不一致，worker 以 WARN 事件记录后按计划继续
  （漂移不阻断，见 consume_outbox.py 注释）
- **影响**：质量分流表指向与影子表指向可能不是同一张表（若真实链路搬数据会写错
  目标）；mock 引擎下不影响演示结论，但属生成计划的一致性问题
- **修复建议**（未实施，待排期）：生成域出参校验（计划内 valid_sink 必须与
  目标宽表派生规则一致）或消费端按计划重算对齐，二选一排期
- **关联**：T-SHADOW-01；S5 生成引擎质量契约口径

### T-CONSOLE-01：控制台刷新后显示态未持久化

- **状态**：Open（本 PR 已修 `actorName`；`actorRole` 待补）
- **发现**：S10 场景 A 截图取证（2026-10-16，F5 刷新后顶部栏恒显「未登录」）
- **现象**：`frontend/src/stores/actor.ts` 的 `actorName` 初值硬编码 `'未登录'`
  且不从 localStorage 恢复（只有 `actorId` / `projectId` 恢复）→ 刷新后身份显示
  丢失（`AppShell.vue` 顶部栏）；`actorRole` 同样未持久化（刷新后角色徽标为空）
- **影响**：显示态不完整；鉴权**不受影响**（`X-Actor-ID` 由 `client.ts` 拦截器
  每次经 `getActorId()` 现读 localStorage）。截图/演示需手动 bind 才能显示身份
- **处置**：本 PR（`fix/S10-console-refresh-defects`）修 `actorName`——刷新时从
  `getActorId()` 恢复（1 行）；`actorRole` 需持久化方案（localStorage 增列或缓存
  bootstrap 响应），另行排期
- **关联**：`fix/S10-console-refresh-defects`；`docs/demo/演示回放.md` §8.9

## 四、AMEND 追加件登记

| 编号 | 标题 | 状态 | 关联基线 |
|------|------|------|---------|
| AMEND-001 | Bootstrap 错误码补充 | Accepted | SPEC v1.0.3-Baseline 6.4 |
| AMEND-002 | 连接域错误码补充 | Accepted | SPEC v1.0.3-Baseline 6.4 |
| AMEND-003 | 探查域与文件资产域错误码补充 | Accepted | SPEC v1.0.3-Baseline 6.4 |
| AMEND-004 | 生成域错误码补充 | Accepted | SPEC v1.0.3-Baseline 6.4 |
| AMEND-005 | Harness 与版本域错误码补充 | Accepted | SPEC v1.0.3-Baseline 6.4 |
| AMEND-006 | 执行域与引擎域错误码补充 | Accepted | SPEC v1.0.3-Baseline 6.4 |
| AMEND-007 | 质量发布与监督回滚域错误码补充 | Accepted | SPEC v1.0.3-Baseline 6.4 |
| AMEND-008 | 审计证据与评测安全进化域错误码补充 | Accepted | SPEC v1.0.3-Baseline 6.4 |
| AMEND-009 | 生成运行响应追加 pipeline_version_id（API-05/06 出参） | Accepted | SPEC v1.0.3-Baseline 6.1（API-05/06） |
| AMEND-010 | 生成运行**列表**出参追加 pipeline_version_id（API-05 列表） | Accepted | SPEC v1.0.3-Baseline 6.1（API-05 列表） |

## 五、操作日志（破例与流程偏差）

### 2026-10-16：文档措辞变更破例直推 master（`381b5a2`）

- **内容**：`docs/demo/演示回放.md` §8.9 与 `docs/stages/assets/S10-demo-a/README.md`
  的「Studio 未截图」口径改为简明版（人类指定措辞：PR #15 已修 / 下次取证补至 6 张）。
- **性质**：**非 Baseline 的文档措辞**、极小改动（2 文件 +9/−11，无代码 / 契约 /
  配置 / 密钥影响；diff 仅两个 hunk）。
- **经过**：当轮人类合并 PR #15 后工作区停在 `master`，会话侧误判为「更新 PR #15」
  而直接 `commit + push` 到 master；人类裁决**保留**（内容正确）。
- **口径澄清（防复发）**：**本条不构成「可以随便推 master」的先例**。自本条起：
  **所有改动（含文档、单行）一律走 PR，除非人类明确说「这次直推」。**
- **关联**：PR #16（过期状态引用清理 + 本条登记）。
