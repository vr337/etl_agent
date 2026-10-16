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
| S7 | 1 | SeaTunnel REST `submit-job` NPE | CLI 通道已落地（部分完成） | **T-ENGINE-01**（AC-16 闭环待 T-SHADOW-01） |
| S10 | 7 | 生成模板硬编码 PG 驱动 + worker 容器内寻址不可达 + worker 镜像漏插件索引 | **已修（真引擎通到执行阶段）** | ✅ **T-ENGINE-02 Closed** |
| S10 | 8 | 生成的 sink `query` 非完整 INSERT，Doris 解析失败 | **已修（run20 落数 1000 行）** | ✅ **T-ENGINE-03 Closed** |
| S7 | 6 | 调度环境继承口径 | 本阶段可收尾 | ✅ 人类裁决 A |
| S10 | 1 | demo seed 脏行 3 不可落库（address 超宽） | 演示数据形态待裁决 | **T-DEMO-01** |
| S10 | 2 | 非演示路径 12 处路由漏 commit | 独立排期 | **T-COMMIT-01** |
| S10 | 3 | 审计钩子未接线，证据账本恒空 | 已修复（F3） | ✅ 人类裁决已闭环 |
| S10 | 4 | 影子表执行通道为 Logging，不建真实 Doris 表 | 独立排期 | **T-SHADOW-01** |
| S10 | 5 | 质量契约 valid_sink 与派生影子表名漂移 | 独立排期 | **T-SHADOW-02** |
| S10 | 6 | 控制台刷新后显示态未持久化（actorRole 空；name 本 PR 已修） | 独立排期 | **T-CONSOLE-01** |
| — | — | HOCON 的 sink/URL/driver 不应由 LLM 决定（真 LLM 接入前置） | 真 LLM 接入时 | **T-LLM-01** |
| S10 | 9 | 引擎 metrics 跨 task 重试累加，失败作业行数虚高（4008=4×1002） | 独立排期 | **T-METRICS-01** |

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
- **仍未闭环**：**AC-16「Doris 有真实数据」未完全达成**——T-ENGINE-02（生成模板）与
  T-ENGINE-03（sink SQL 形态）已修，run20 实证 Doris `order_wide` 落 **1000 行真实数据**；
  剩余一环是**脏行分流**（T-SHADOW-01）——脏行导致批次整批回滚、作业终态 FAILED。
  T-SHADOW-01（影子表通道）在真实链路下同样仍未实现（当前为裸 INSERT 直插目标表，
  非原子 Swap 语义）
- **关联**：SPEC S-Q4（SeaTunnel 部署形态）；T-ENGINE-02（Closed）；T-ENGINE-03（Closed）；
  T-SHADOW-01（AC-16 最后一环）
- **当前镜像**：`flowpilot/seatunnel:2.3.13`（compose e2e/exec）

### T-ENGINE-02：生成模板不感知 connector_type + 容器内寻址不可达 + worker 镜像漏插件索引 —— **Closed**

- **状态**：**Closed**（2026-10-16，fix/T-ENGINE-02）——真引擎已通到「作业执行阶段」，
  DoD 达成（不管最终 SUCCEEDED/FAILED）；落库最后一公里转 **T-ENGINE-03** 独立立项。
- **发现**：T-ENGINE-01 步骤 4 前置判定（生成 HOCON 后读 checkpoint 候选正文）
- **缺陷 1（驱动/协议硬编码）**：HOCON 模板 source/sink 均硬编码
  `driver = "org.postgresql.Driver"` + `jdbc:postgresql://`，**不按连接的
  `connector_type`（MYSQL / DORIS）渲染**。物化后 source 指向 MySQL 容器
  （MySQL 协议）、sink 指向 Doris FE（MySQL 协议）→ PostgreSQL 驱动连 MySQL
  协议端口，JDBC 握手即败。mock 时代引擎不出水故完全不可见。**已修**：
  `llm/mock_provider.py` 按 connector_type 选驱动/URL（MYSQL/DORIS→MySQL 驱动，
  PG 保持；未知回落 PG + warning）。
- **缺陷 2（执行视角寻址）**：物化取连接行 host/port **原值**（演示数据按宿主机视角填
  `127.0.0.1:13306/9030`），而引擎提交发生在 **worker 容器内**——`127.0.0.1` 是它自己，
  source/sink 均不可达。**已修**：新增 `ETL_AGENT_HOST_MAP` 执行视角映射
  （worker 物化时替换 `127.0.0.1:13306→mysql:3306` / `127.0.0.1:9030→doris-fe:9030`；
  API 探测路径用原值不动）。
- **缺陷 3（真实引擎 metrics 键）**：真实引擎键名与 mock 不同，`source_rows/sink_rows`
  恒空。**已修**：`execution/metrics.py` 增加真实键兜底
  （`TableSourceReceivedCount`→source_rows；`TableSinkWriteCount`/`SinkCommittedCount`→sink_rows）。
- **缺陷 4（worker 镜像漏插件索引，步骤 4 实测暴露）**：worker 镜像只 COPY 了 4 个白名单
  jar，**漏 COPY `/opt/seatunnel/connectors/plugin-mapping.properties`**（插件名→jar 工件名的
  翻译索引）。SeaTunnel 提交时先查该表把 `Jdbc` 译成 `connector-jdbc` 再找 jar；索引缺失
  → `PluginIdentifier{pluginName='Jdbc'} not found`，与 jar 是否齐全无关（实证：把引擎全部
  84 个 jar 拷进 worker 仍失败）。**已修**：`infra/Dockerfile.worker` 追加同源 stage COPY
  （全量、不裁剪——该文件是惰性索引，多余映射无害）；`test_deploy_contract.py` 补两条静态
  断言（必须有该文件 + connectors 目录只含白名单 jar，防调试污染泄漏）。
- **run19 实证（2026-10-16 19:31，真引擎 real-cli）**：13 步跑到 STEP 12，
  `status=RUNNING · 源行数=3006 · 目标行数=3006`（20s 轮询窗口内未到终态）；
  终态 `FAILED / engine_status=FAILED / failure_stage=**run**`（非 submit），
  job_id=1163439178180984833。引擎侧插件解析成功
  （`needed jar urls [connector-jdbc-2.3.13.jar]`），源端真实读到数据
  （`SourceReceivedCount=4008`，含中文地址行）→ **驱动 + 寻址 + 插件全部生效**。
  证据账本 `valid=true · event_count=9`。**判定：链路已通到执行阶段，本 PR 达成 DoD**。
- **剩余阻断 → T-ENGINE-03**：最终 FAILED 是 sink SQL 不完整（见下），属生成域新缺陷。
- **关联**：T-ENGINE-01（上游已就绪）；T-ENGINE-03（sink SQL，下游唯一阻断）；
  T-SHADOW-01（影子表通道）；T-SHADOW-02（valid_sink 契约漂移）

### T-ENGINE-03：生成的 sink `query` 非完整 INSERT，SeaTunnel 原样下发致 Doris 解析失败 —— **Closed**

- **状态**：**Closed**（2026-10-16，fix/T-ENGINE-03）——sink 改 `table` 形态后 run20 实证
  Doris `order_wide` 落 **1000 行真实数据**；作业 FAILED 纯因脏行（T-SHADOW-01 范畴），
  链路真通。
- **现象**：run19 作业提交成功、源端读到数据，但 sink 首行即败。引擎日志
  `[FieldNamedPreparedStatement] PrepareStatement sql is:` → **`insert into order_wide`**
  （无列清单、无 `VALUES` 占位符）；Doris FE 解析器报
  `mismatched input ';' expecting {'(', 'VALUES', 'SELECT', ...} (line 1, pos 22)`，
  经 `JdbcConnectorException COMMON-08/COMMON-10` 冒泡为 `table source_db.orders sink throw error`。
- **根因**：SeaTunnel JdbcSink 使用 `query` 选项时**原样作为 PreparedStatement 下发、不补
  VALUES 子句**——`query` 必须是完整可执行 SQL（`insert into t (c1,c2) values (?,?)`）或改用
  `table` 选项由连接器生成。当前生成模板产出 `query = "insert into order_wide"`，既无列清单
  也无占位符 → 任何一行都会失败（**非脏行型**，是 SQL 形态缺陷）。
- **来源**：本项此前已在 T-ENGINE-02 记为「附带（次要）：裸 INSERT 无列清单，列序依赖 JDBC
  默认排序」——**实测该"次要项"是硬阻断**（不是列序问题，是语句不完整）。
- **手工 HOCON 实验现场记录**（2026-10-16，引擎容器 `seatunnel.sh --config` 逐级试，
  四选项缺一不可）：

  | 变体 | 结果 |
  | --- | --- |
  | `table` 单独 | ❌ 客户端校验失败 `options('query') are required because ['generate_sink_sql' == false]` |
  | `+ generate_sink_sql = true` | ❌ 校验失败 `options('database') are required because ['generate_sink_sql' == true]` |
  | `+ database = "doris_dw"` | ❌ SQL 已生成但追加 `ON DUPLICATE KEY UPDATE`（2.3.13 默认 `enable_upsert=true`）→ Doris `mismatched input 'ON' expecting {<EOF>, ';'} (line 1, pos 74280)` |
  | `+ enable_upsert = false` | ✅ **FINISHED** · Read 1001 / Write 1001 / Failed 0 → Doris 落 1001 行 |

  成功时连接器生成的 SQL 形态（**列清单由目标表元数据自动生成、按列名映射**，免疫列序变化）：
  ```sql
  INSERT INTO `doris_dw`.`order_wide` (`order_id`,`customer_id`,`amount`,`status`,`address`,`updated_at`) VALUES (?,?,?,?,?,?)
  UPDATE  `doris_dw`.`order_wide` SET ... WHERE `order_id` = ?
  ```
  `enable_upsert` 语义：2.3.13 默认 `true` → 生成 MySQL 风格 upsert 语句
  （`INSERT ... ON DUPLICATE KEY UPDATE`），**Doris 解析器不支持该语法**；置 `false` 后走
  「INSERT + PK 冲突时 UPDATE」，Doris 接受。
- **修复**：`llm/mock_provider.py::compliant_hocon` sink 段改 `table` 形态，四选项缺一不可
  （`table` / `database` / `generate_sink_sql = true` / `enable_upsert = false`）；
  `table`←`sink_profile.table_name`、`database`←`sink_profile.schema_name`（不硬编码）；
  url/driver 仍走 `_jdbc_block`（T-ENGINE-02 的连接器感知渲染）；旧 `query` 形态保留为注释。
- **run20 实证**（真引擎 real-cli，13 步）：执行终态 `FAILED / failure_stage=run`，
  job `1163447565283229697`；**Doris `order_wide` = 1000 行真实数据**（order_id 1..1000，
  含中文地址）；引擎日志 SQL 为上述 `table` 形态（**无 `ON DUPLICATE KEY`**）。
  FAILED 原因是**脏行**：Doris 拒收日志
  `Reason: column_name[amount], null value for not null column, type=DECIMAL64(18, 2)`
  （源行 9001 amount NULL）→ 该行所在批次（`batch_size`=1000 的第二批，含 9001/9002）
  整批回滚，故 1002−1000=2（1 脏行 + 1 同批连带）。**错误分流缺失属 T-SHADOW-01**。
- **影响**：AC-16 **部分闭环**——真引擎链路已落数；「脏行不毒化整批」的分流仍待 T-SHADOW-01。
- **关联**：T-ENGINE-02（上游已通到执行阶段）；T-SHADOW-01（脏行分流，AC-16 最后一环）；
  T-SHADOW-02（valid_sink 契约漂移）；T-DEMO-01（源端脏行口径）

### T-LLM-01：HOCON 的 sink/URL/driver 形态不应由 LLM 决定（新，Open，不阻塞）

- **状态**：Open（2026-10-16 记账，不阻塞当前排期）
- **背景**：T-ENGINE-02（驱动/URL 按 connector_type 渲染）与 T-ENGINE-03（sink `query`→`table`
  四选项形态）修改的是 `llm/mock_provider.py` 里的 HOCON 渲染。当前 mock provider 是
  **确定性模板**，这些修复直接落在模板上；但若将来接入**真 LLM** 生成 HOCON，同样的坑会重演。
- **结论/建议**：真 LLM 接入时，HOCON 的 **sink 形态 / JDBC URL / driver** 必须由后端
  **确定性模板**渲染，LLM 只负责**语义层**（选表、选字段、转换规则），不决定协议与 SQL 形态。
- **动作（prompt 约束须同步的键）**：真 LLM 接入前，把 mock_provider 的确定性渲染抽为可复用的
  「HOCON 渲染器」，并在 prompt 里明确禁止 LLM 自定以下键：
  `url`（按 `connector_type` 选 `jdbc:mysql://` / `jdbc:postgresql://`）、
  `driver`（`com.mysql.cj.jdbc.Driver` / `org.postgresql.Driver`）、
  sink 段 `table`（← 目标 profile 的 `table_name`）、`database`（← 目标 profile 的 `schema_name`）、
  `generate_sink_sql = true`、`enable_upsert = false`；
  且**禁止**在 sink 段回退到 `query = "insert into <表>"` 形态（T-ENGINE-03 已证必败）。
- **关联**：T-ENGINE-02（Closed）；T-ENGINE-03（Closed）；`llm/mock_provider.py`

### T-METRICS-01：引擎 metrics 跨 task 重试累加，失败作业的行数虚高（新，Open）

- **状态**：Open（2026-10-16，T-ENGINE-03 run20 实证）
- **现象**：run20 作业终态 FAILED，`execution_runs.source_rows = sink_rows = **4008**`，
  而源表实际只有 **1002** 行（Doris 实收 1000 行）。4008 = **4 × 1002** ——
  引擎（SeaTunnel Zeta）的 `SourceReceivedCount` / `SinkWriteCount` 是**跨 task 重试累加**的
  计数器：作业每次 task 尝试都重新读源，计数持续累加而非重置。
- **影响**：失败/重试作业下 `source_rows` / `sink_rows` **不可当行数用**（会按尝试次数倍增）；
  成功作业（单次尝试）不受影响。AC-16 的权威行数应取 **Doris `count(*)`**，
  不能只信 API 的 `source_rows/sink_rows`。
- **修复方向**（待裁决）：metrics 只取**最终一次 task 尝试**的计数（取最新快照而非累计和），
  或按 task 维度去重/标记 attempt；亦可改为在作业成功时以 `SinkCommittedCount` 为准。
- **关联**：T-ENGINE-02（缺陷 3 真实引擎 metrics 键映射，本项是其语义边界）；
  `execution/metrics.py`；AC-16（行数取证口径）

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
