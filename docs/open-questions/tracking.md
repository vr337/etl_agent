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
| S7 | 1 | SeaTunnel REST `submit-job` NPE | **已修（AC-16 真闭环）** | ✅ **T-ENGINE-01 Closed** |
| S10 | 7 | 生成模板硬编码 PG 驱动 + worker 容器内寻址不可达 + worker 镜像漏插件索引 | **已修（真引擎通到执行阶段）** | ✅ **T-ENGINE-02 Closed** |
| S10 | 8 | 生成的 sink `query` 非完整 INSERT，Doris 解析失败 | **已修（run20 落数 1000 行）** | ✅ **T-ENGINE-03 Closed** |
| S7 | 6 | 调度环境继承口径 | 本阶段可收尾 | ✅ 人类裁决 A |
| S10 | 1 | demo seed 脏行 3 不可落库（address 超宽） | **已修（VARCHAR(500)，重 init 1003 行）** | ✅ **T-DEMO-01 Closed** |
| S10 | 2 | 非演示路径 12 处路由漏 commit | **已修（fix/T-COMMIT-01，12 处 + 12 测试）** | ✅ **T-COMMIT-01 Closed** |
| S10 | 3 | 审计钩子未接线，证据账本恒空 | 已修复（F3） | ✅ 人类裁决已闭环 |
| S10 | 4 | 影子表/错误表执行通道为 Logging + 无质量分流（双路） | **已修（PR-A + PR-B，AC-16 真闭环）** | ✅ **T-SHADOW-01 Closed** |
| S10 | 5 | 质量契约 valid_sink 与派生影子表名漂移 | **已修（命名统一到 `_shadow`）** | ✅ **T-SHADOW-02 Closed** |
| S10 | 6 | 控制台刷新后显示态未持久化（actorRole 空；name 已修） | **已修（actorRole 持久化）** | ✅ **T-CONSOLE-01 Closed** |
| — | — | HOCON 的 sink/URL/driver 不应由 LLM 决定（真 LLM 接入前置） | 真 LLM 接入时 | **T-LLM-01** |
| S10 | 9 | 引擎 metrics 跨 task 重试累加，失败作业行数虚高（4008=4×1002） | 独立排期 | **Closed (Won't Fix)** |
| S10 | 12 | 未覆盖脏行类型（类型不匹配/超长）仍可能毒化 batch | 独立排期 | **Closed (Won't Fix)** |
| S10 | 10 | doris-init 注释与 PRD 10.5 #4 矛盾（本 PR 不动） | **已修（fix/final-gate：注释改为 PRD 10.5 #4 例外条款口径）** | ✅ **T-DOCS-01 Closed** |
| S10 | 11 | etl_quality_summary 在 src/ 下零写入点（死表） | **已修（fix/final-gate：删表定义，仅留 DROP 清理残留）** | ✅ **T-SHADOW-04 Closed** |
| S10 | 12 | 数据面 DDL 失败未映射到 ENGINE 族（run21 暴露，本 PR 不动） | **已修（关键字映射 → ENGINE）** | ✅ **T-CLASSIFY-01 Closed** |
| S10 | 13 | 发布四门禁散在三处 + 门禁函数曾有僵尸 `executor` 参数 | **已修（第 4 道判据收拢 + 文档改真）** | ✅ **T-PUBLISH-01 Closed** |
| S10 | 14 | Swap 失败的回退状态未区分（swap 已执行 / 未执行） | 独立排期 | **Closed (Won't Fix)** |
| S10 | 15 | publish 状态无可视化渠道（无字段 / 前端不渲染 events） | **已修（前端由 events[] 派生 + 角标）** | ✅ **T-OBS-01 Closed** |
| S10 | 16 | 引擎容器（worker/seatunnel/doris）未设 TZ，与源库 CST 差 8h → 水位谓词全量拒收 | **已修（fix/infra-tz；run28 复验 1000/2）** | ✅ **T-TZ-01 Closed** |
| S10 | 17 | 质量契约未声明阈值 → 拒绝率 100% 仍判 PASSED（fail-open） | **已修（fix/final-gate：契约显式声明 `max_reject_rate: 0.05`）** | ✅ **T-QUALITY-01 Closed** |
| S10 | 18 | Pipeline Studio 门禁 14/14 / HOCON 正文 / 版本链 / 候选数为硬编码演示态，未标「示意」 | 独立排期 | **Closed (Won't Fix)** |
| S10 | 19 | Safety 页 Benchmark 六项门禁回落演示值 | **已修（fix/S10-console-contract-a，PR #27）** | ✅ **T-CONSOLE-03 Closed** |
| S10 | 20 | Studio 读 `version_no`（实际 `version_number`）→ 渲染 `Vundefined` / `VNaN` / 版本链恒 `V7` | **已修（PR #25，5 处引用全修）** | ✅ **T-CONSOLE-04 Closed** |

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
- **AC-16 真闭环**（run25 实证）：SPEC AC-16 全段已通——连接→探查→生成→门禁→审批→
  执行→影子表→**双路分流**→**原子 Swap**。`order_wide`=**1000**（swap 后正式表）、
  `order_wide_shadow`=0（swap 后指向旧数据）、`order_wide_error`=2；
  事件流含 `execution.published`（原子发布完成：order_wide ← order_wide_shadow）
- **关联**：SPEC S-Q4（SeaTunnel 部署形态）；T-ENGINE-02（Closed）；T-ENGINE-03（Closed）；
  T-SHADOW-01 PR-A（PR #20，已合）；T-SHADOW-01 PR-B（原子 Swap，已接线）
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
- **影响**：AC-16 **已真闭环**——「脏行不毒化整批」的分流由 T-SHADOW-01 **PR-A** 完成
  （run24 实证 1000 + 2），原子 Swap 由 **PR-B** 完成（run25 实证主表 1000）。
- **关联**：T-ENGINE-02（上游已通到执行阶段）；T-SHADOW-01 PR-A + PR-B（已 Closed）；
  T-SHADOW-02（valid_sink 契约漂移，已 Closed）；T-DEMO-01（源端脏行口径）

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

### T-METRICS-01：引擎 metrics 跨 task 重试累加，失败作业的行数虚高（**Closed (Won't Fix)**）

- **状态**：**Closed (Won't Fix)**（2026-10-17，fix/final-gate，人类裁决不修直接关）
- **不修原因**：
  1. **只影响失败/重试作业**，成功作业（单次尝试）计数正确 → 演示与生产主链路无影响；
  2. **取证口径已绕开**：AC-16 的权威行数改取 **Doris `count(*)`**，不再依赖 API 的
     `source_rows/sink_rows`，本缺陷不再污染任何证据；
  3. 修复需在 `execution/metrics.py` 引入 **task attempt 维度**（按 attempt 去重 / 取最新
     快照），等于改「引擎指标 → 运行指标」的语义，收益（仅失败作业的诊断精度）不抵
     改动面与回归风险，M2 收尾不做。
- **遗留口径**：失败作业的 `source_rows` / `sink_rows` **不可当行数用**（会按尝试次数倍增），
  排障时以 Doris 侧计数为准。此口径已写入本条，后续若接真 LLM / 生产排障面板再评估。
- **现象**：run20 作业终态 FAILED，`execution_runs.source_rows = sink_rows = **4008**`，
  而源表实际只有 **1002** 行（Doris 实收 1000 行）。4008 = **4 × 1002** ——
  引擎（SeaTunnel Zeta）的 `SourceReceivedCount` / `SinkWriteCount` 是**跨 task 重试累加**的
  计数器：作业每次 task 尝试都重新读源，计数持续累加而非重置。
- **影响**：失败/重试作业下 `source_rows` / `sink_rows` **不可当行数用**（会按尝试次数倍增）；
  成功作业（单次尝试）不受影响。AC-16 的权威行数应取 **Doris `count(*)`**，
  不能只信 API 的 `source_rows/sink_rows`。
- **若将来要修的方向（本次未采纳）**：metrics 只取**最终一次 task 尝试**的计数
  （取最新快照而非累计和），或按 task 维度去重/标记 attempt；亦可改为在作业成功时以
  `SinkCommittedCount` 为准。
- **关联**：T-ENGINE-02（缺陷 3 真实引擎 metrics 键映射，本项是其语义边界）；
  `execution/metrics.py`；AC-16（行数取证口径）

### T-DEMO-01：demo seed 脏行 3 不可落库（address 超列宽）

- **状态**：**已修（fix/T-COMMIT-01 一并处理，人类 2026-10-17 裁决）**
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
- **修复**：源表 `address` 加宽至 **VARCHAR(500)**（`infra/e2e/mysql-init/01-seed.sql:32`），
  400 字符脏行得以落库；重 init 后实测 **1003 行**（1000 干净 + 3 脏），
  seed 脚本不再于第 79 行中止（`CREATE USER demo_reader` 等尾部语句恢复执行）。
- **演示数据形态随之改变**：一次真跑的质量口径从 `1002 / 1000 / 2（0.20%）` 变为
  **`1003 / 1000 / 3（0.30%）`**，错误码多一条「地址超长」。
  ⚠️ §8.9 的 A2/A3 截图仍是 run17（1002/2）口径，与重跑结果不再一致 ——
  需要另起一次取证才能刷新（未在本次范围）。
- **关联**：S10 T-22 demo-scenario-a；`etl_quality_summary` 表注释

### T-COMMIT-01：非演示路径 12 处路由漏 commit

- **状态**：**已修（fix/T-COMMIT-01）**
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
- **修复**：照 F1 范式在**路由层**补 `await session.commit()`（12 处，`connections` 11 +
  `files` 1），探针同款（`_spy_commits` from `tests/test_commit_persistence.py`）。
  ⚠️ **其中 4 个 service 有不可逆外部副作用**（`create_rotation`→`provider.store`、
  `stage_migration`→`source.read`+`target.store`、`execute_destruction`→`provider.destroy`、
  `validate_rotation`→`provider.read`），发生在 flush 之前 —— 漏 commit 时
  「凭据已写/已抄/已销毁，DB 记录消失」，`execute_destruction` 尤其致命
  （不可逆 + 无审计记录）。本次修复已消除该窗口。
- **回归**：`tests/test_commit_persistence_governance.py` 12 条 commit-spy
  （负向验证：撤掉修复 12 failed / 装上 12 passed）；全量 **955 passed, 2 skipped**；
  mypy 259 files 无问题；ruff check 我的 3 文件全过（`alembic/env.py` 的 2 个
  I001 为 clean master 既有基线，非本次引入）。
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

- **状态**：**Closed（PR-A + PR-B 全落）**——PR-A（fix/T-SHADOW-01a，PR #20）已合：
  质量分流 + 影子表/错误表真实通道 + 命名统一（run24 实证 shadow=1000 / error=2）；
  PR-B（fix/T-SHADOW-01b）：`_finalize_run` 接线 `publish_run` + 注入 `DorisSwapExecutor`，
  四门禁 → 原子 Swap → 正式表发布（run25 实证 **order_wide=1000**）。
  **AC-16 真闭环**（SPEC 全段：连接→探查→生成→门禁→审批→执行→影子表→分流→Swap）
- **状态口径澄清（2026-10-17）**：此前会话汇报中曾把本项说成「仍 Open」，**属口误**。
  以本条为准：**T-SHADOW-01 自 PR #20 + PR-B 起已 Closed**，§二 汇总表亦为 Closed。
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
- **PR-A 落盘内容（2026-10-16）**：范围比原描述大——不止「换通道」，而是把
  **影子表 DDL + 质量分流 + 命名统一**三件事接线：
  - **生成侧双路分流**（`llm/mock_provider.py`）：source 打 `plugin_output = "src"`，
    两个 `Sql` transform 按质量契约谓词切 valid / error，两个 `Jdbc` sink 用
    `plugin_input` 分别消费 → `<目标表>_shadow` / `<目标表>_error`
    （SeaTunnel 2.3.13 实测：同类型 plugin 可重复声明、`plugin_input/output` 路由生效；
    ⚠️ `LIMIT/OFFSET` 不支持，时间函数须 `CURRENT_TIMESTAMP`，`TIMESTAMP` 字面量须 ISO 格式）
  - **质量规则三条**（对齐 seed 脏行设计，FR-22 既有范围，**不需 AMEND**）：
    `order_id IS NOT NULL`（防御性）/ `amount IS NOT NULL` / `updated_at <= CURRENT_TIMESTAMP`；
    `code` 标签 `QUALITY_NOT_NULL_PK` / `QUALITY_NOT_NULL_AMOUNT` / `QUALITY_FUTURE_WATERMARK`
  - **error_code 用 CASE 取首命中**（单 error 分支，一行恰好一条错误记录 →
    `rejected_rows` 不被重复计数污染；code 单值可聚合，合 PRD 第 10 章 `rules[].code`）
  - **错误表运行期幂等建表**（`execution/shadow.py`）：`CREATE TABLE IF NOT EXISTS
    <目标表>_error`，业务列**全可空**（不能用 `LIKE`——目标表 `amount NOT NULL`，
    照抄会把脏行再拒一次）+ `error_code` / `error_reason` 两列
  - **接线**（`worker/tasks/consume_outbox.py`）：抽 `_connection_values`（与物化同源，
    含 host_map 执行视角重写）→ `_build_shadow_executor` 工厂按每次运行的 sink 坐标
    构造真实 `DorisShadowTableExecutor`；`_run()` 注入该工厂
  - **双 sink metrics 分键**（`execution/metrics.py`）：`sink_rows` 取 `_shadow` 后缀
    sink 键、`rejected_rows` 取 `_error` 后缀键，**不用扁平合计**（合计会让 rejected 恒 0）
- **命名统一**（T-SHADOW-02 修法）：契约 `valid_sink` 改为 `<目标表>_shadow`，
  与 `execution.shadow` 派生名同源
- **run21 暴露并已修：影子表 DDL 非幂等**（既有缺陷，PR-A 接线后首次真实执行 DDL 才暴露）
  - 现象：作业在**提交前**失败（`stage=dispatch`），
    `OperationalError: errCode=2, Table 'order_wide_shadow' already exists`
  - 根因：`create_shadow_table_sql` 生成 `CREATE TABLE <t>_shadow LIKE <t>` **缺
    `IF NOT EXISTS`**，而影子表已被 `doris-init/01-init.sql:43-44` 预建；
    此前不可见是因为 `shadow_executor` 一直回落 Logging 通道、DDL 从未真正执行
  - 修复：加 `IF NOT EXISTS`（Doris 实证 `CREATE TABLE IF NOT EXISTS <新> LIKE <已有>`
    首建 OK、二次幂等 OK、结构与原表一致）；补两条断言（影子表幂等 / 两处建表同族一致）
- **batch 毒化已由双路设计在路由层消除**（不再需要方案 A/B/C）：valid 分支的
  `where r1 and r2 and r3` 已把两条脏行排除在影子表之外，影子表只收干净行，
  NOT NULL 不会再被打；run20 的 1002→1000 损失是因为当时**所有行都直插主表**。
  若 run22 仍出现毒化，再逐行方案（见 PR-B 记录）
- **run22 暴露并已修：SeaTunnel Sql transform 不支持 `NOT (...)` 包裹 `IS NOT NULL`**
  - 现象：error 分支的 `where not (order_id IS NOT NULL) or ...` 在**运行期**抛
    `TransformException: COMMON-05 Unsupported SQL Expression: NOT (id IS NOT NULL)`；
    校验期（`--check` 与门禁 14 项）**不报**，只在真跑时炸——静态门禁抓不到此类
  - 修复：error 分支改用**直接否定态**（`order_id IS NULL` / `amount IS NULL` /
    `updated_at > CURRENT_TIMESTAMP`），CASE 同样用否定态；`_QUALITY_RULES` 每条规则
    改为携带 `pass`（通过态，写契约与 valid 分支）/ `fail`（否定态，写 error 分支）两个表达式
  - 实证：`where (id IS NULL) or (amt IS NULL) or (ts > CURRENT_TIMESTAMP)` → FINISHED 4/1
  - 教训：**WHERE 谓词形态必须真跑验证**，`--check`/门禁只校验结构与占位符，不校验
    Calcite 表达式支持面
- **关联**：T-ENGINE-01（AC-16 最后一环）；T-SHADOW-02（命名统一已在 PR-A 内）；
  T-SHADOW-04（etl_quality_summary 死表）；T-METRICS-01（重试累加，独立仍 Open）；
  T-CLASSIFY-01（run21 失败分类未映射）

### T-CLASSIFY-01：数据面 DDL 失败未映射到 ENGINE 族（新，Open）

- **状态**：**已修（chore/M2-cleanup，取建议 ② 关键字映射，无需 AMEND）**
- **现象**：run21 作业在建表阶段失败，`execution_runs.failure_category = **UNKNOWN**`、
  `failure_stage = dispatch`、`diagnosis` 是 aiomysql 原样的
  `OperationalError: (1105, "errCode = 2, ...")` —— 未被 `classify_failure` 归入
  既有 9 类（ENGINE / ARTIFACT_DRIFT / …）
- **影响**：诊断与重试决策读不出失败族别（`retryable=True` 是兜底值，非判决）；
  演示与排障时运维看到的是原始堆栈摘要而非分类结论
- **根因**：`execution/shadow.py` 的 `DorisShadowTableExecutor` 直接抛 aiomysql 的
  `OperationalError`（未包成本域异常），`classify_failure` 只认本域异常与已知引擎异常
- **修复**（取建议 ②，最小改动、不用 AMEND）：`classifiers._KEYWORD_RULES` 新增
  数据面 DDL 规则（`ddl` / `alter table` / `create table` / `drop table` /
  `already exists` / `errcode` / `1105` / `数据面`）→ **ENGINE、可重试**。
  **必须排在 CONNECTIVITY / SINK_WRITE 之前**：上游消息含「errCode = 2」这类
  无意义提示词，落到连通族会误导重试决策。`1105` 是 Doris 常见
  「存储/元数据不可用」码（run21 实测形态）。
  建议 ①（新增 `BIZ_503_DATA_PLANE_UNAVAILABLE` 并包本域异常）留待真要改错误码表时再做。
- **关联**：T-SHADOW-01（run21 暴露）；`execution/metrics.py`；SPEC 6.4 错误码体系

### T-PUBLISH-01：发布四门禁散落在三处（新，Open）

- **状态**：**已修（chore/M2-cleanup：第 4 道判据收拢 + 文档说真话）**
- **发现**：T-SHADOW-01 PR-B 探查（2026-10-16）
- **现象**：`publish.py` 模块 docstring 原称「四道门禁」都在 `evaluate_publish_gate`，
  实际只判 ①②；③（Schema 一致）在 `execute_atomic_swap` 内、④（幂等）在 `publish_run` 内。
  且 `evaluate_publish_gate` 白收了一个**未使用的 `executor` 参数**（执行通道依赖漏进了
  纯决策函数）
- **修复（本 PR）**：
  1. **第 4 道收拢** —— `evaluate_publish_gate` 其实早已判幂等（PR-B 后留着），
     但 `publish_run` 又**另查一次** `current_publish_state`，判据两处、事件流查询翻倍。
     现把结论并入 `PublishDecision.already_published`，`publish_run` 直接取用
     → 1/2/4 道同处判定，第 3 道仍是唯一外置判据（原因见下）。
  2. **文档改真** —— 模块 docstring 的分布表改注「1/2/4 在 `evaluate_publish_gate`、
     3 在 `execute_atomic_swap`」，并写清「③ 不挪」的理由（读两侧 `DESC` 属执行期动作，
     挪进来会 Doris 被读两次）与本次收拢的范围。
  3. **回归钉住** —— 新增 `test_gate_reports_idempotency`：未发布时
     `already_published=False`，发布后 `True` 且 `reason` 以「幂等」开头；
     既有 `test_atomic_swap_idempotent` 继续断言只 swap 一次。
- **死字段已清（fix/final-gate 补做）**：`PublishTargets.error_table` /
  `.quality_contract` 两个字段此前「填了没人读」（`resolve_targets` 赋值、`publish_run`
  从不读取），本 PR 已删两字段并同步 `tests/execution/test_publish.py` 的构造与断言。
  错误表由 `consume_outbox._ensure_error_table` 独立派生，质量契约由 `routing` 直接
  消费，删除不影响任何链路。
- **关联**：T-SHADOW-01 PR-B；`execution/publish.py` / `atomic_swap.py`

### T-SWAP-01：Swap 失败的回退状态未区分（**Closed (Won't Fix)**）

- **状态**：**Closed (Won't Fix)**（2026-10-17，fix/final-gate，人类裁决不修直接关）
- **不修原因**：
  1. **失败窗口极窄**：swap 是 Doris 元数据级瞬时操作（`ALTER TABLE ... REPLACE`），
     正常路径不会失败；run20~run28 十次真实运行**零发生**；
  2. **人工判定成本很低**：真发生半提交时，`SHOW TABLES` + 两侧 `DESC` 即可判定
     「表是否已换名」，运维侧不需要事件流提供这个信息；
  3. **修它要动事件契约**：给 `execution.publish_failed` 增「未执行 / 已执行未确认」
     状态位，属事件语义变更（需 AMEND 并同步前端/证据口径），收益不抵成本。
- **遗留口径**：`publish_failed` 事件仍只记异常类型名；**排障时以 Doris 侧实际表名
  与结构为准**，不要从事件反推「是否已 swap」。
- **发现**：T-SHADOW-01 PR-B 探查（2026-10-16，人类裁决「本 PR 不做」）
- **现象**：`publish_run` 在 `execute_atomic_swap` 抛异常时记 `execution.publish_failed`
  后原样抛出，`_finalize_run` 一律把运行置 FAILED（ENGINE / finalize）。但
  **「swap 本身失败」与「swap 已成功、后续落账失败」两种状态没有区分**——
  事件消息只记 `{type(exc).__name__}`，运维无法从事件反推「表是否已经换了名字」
- **影响**：swap 是元数据级瞬时操作，失败窗口极窄；但一旦真发生（Doris 半提交），
  现状会让排障只能去 Doris 侧查 `SHOW TABLES` 人工比对
- **若将来要修的方向（本次未采纳）**：在 `publish_failed` 事件里区分
  「未执行 / 已执行未确认」，或引入一次执行后校验（`DESC` 两侧再比一次）
- **关联**：T-SHADOW-01 PR-B；`execution/publish.py:205-221`

### T-OBS-01：publish 状态无可视化渠道（**Closed，PR #27**）

- **状态**：**Closed（PR #27，fix/S10-console-contract-a）**——前端改为由
  `ExecutionDetailView.events[]` 派生发布态（`execution.published` /
  `execution.publish_blocked` → 角标），`events[]` 从「类型已定义无人消费」变为
  运行中心真实渲染；同时删掉了「伪造正常状态」的魔法兜底。
  **本条此前状态行误留 Open，本次（fix/final-gate）修正为 Closed。**
- **发现**：T-SHADOW-01 PR-B 方案评审（2026-10-16，人类提问「blocked 时运维从哪看到」）
- **现象**（三处缺口）：
  1. `ExecutionRun` **没有** publish / swap / 发布状态列（SPEC 5.2 未定义，属契约外）
  2. `publish.current_publish_state()`（派生 PUBLISHED / BLOCKED / READY）**只被
     `publish.py` 内部**用于幂等判断，**从未对外暴露**
  3. 前端 `RuntimeCenter.vue` 调了 `getExecution()` 但**只用 `qualityReport`，不渲染
     `events[]`**（`types.ts:246-247` 的 `ExecutionDetailView.events` 类型已定义、
     **无任何组件消费**）
- **当前唯一渠道**：`GET /api/v1/executions/{id}`（API-12）返回完整 `events[]`，
  `execution.publish_blocked` 在其中（level=WARN, stage=finalize）→ **运维必须主动
  翻事件流**，前端控制台看不到
- **PR-B 的最小补偿**：`_finalize_run` 把「发布被阻断（原因）」写进
  `execution.succeeded` 事件的 message，让运维在**不需要翻事件流**的情况下
  从终态原因得知「成功但未发布」
- **修复（已落，PR #27）**：走的是方向 ②——前端运行中心渲染 `events[]` 并派生
  `execution.published` / `execution.publish_blocked` 角标；未做方向 ①（API-12 增
  `publish_state` 派生字段），因为前端已能从事件流派生同一事实，加字段是冗余。
- **关联**：T-SHADOW-01 PR-B；PRD US-11（实时看到执行健康度）/ FR-44；SPEC 6.1 API-12

### T-SHADOW-02：质量契约 valid_sink 与派生影子表名漂移

- **状态**：**已修（随 T-SHADOW-01 PR-A，fix/T-SHADOW-01a）**——契约命名统一到
  `<目标表>_shadow`，与 `execution.shadow.shadow_table_name()` 同源
- **发现**：S10 场景 A 修复后重跑（2026-10-16，事件流 `execution.shadow_contract_drift`）
- **现象**：生成计划 `quality_contract.valid_sink`（旧值 `<目标表>_valid`）与按计划推得的
  影子表名（`shadow_table_name(valid_sink)` = `<目标表>_shadow`）不一致，worker 以 WARN
  事件记录后按计划继续（漂移不阻断，见 consume_outbox.py 注释）
- **影响**：质量分流表指向与影子表指向可能不是同一张表——**且 `publish.resolve_targets`
  会把 `valid_sink` 当影子表名**，若原子发布接线（PR-B）就会 swap 到不存在的
  `<目标表>_valid`；mock 引擎下不影响演示结论，但属生成计划的一致性问题
- **修复**：`llm/mock_provider.py` 的 `_data_plane_tables()` 直接复用
  `execution.shadow` 的 `SHADOW_SUFFIX` / `ERROR_SUFFIX` 常量（单一事实源，不再各写一份）；
  测试夹具 `tests/conftest.py` 两处命名不一致（`:305` 用 `_valid` / `:530` 用 `_shadow`）
  一并统一，并补契约命名一致性断言防复发
- **关联**：T-SHADOW-01（PR-A 内一并修）；S5 生成引擎质量契约口径

### T-DOCS-01：doris-init 注释与 PRD 10.5 #4 矛盾（**Closed**）

- **状态**：**已修（fix/final-gate）**——`infra/e2e/doris-init/01-init.sql` 头部注释
  改为「被拒行写数据面错误表 `<目标表>_error`，**保留原始值与违规原因**（PRD 10.5 第 4 条
  例外条款 / AC-02）；仅报告 / 证据 / 快照只回聚合计数与错误码」，并注明错误表由
  `execution/shadow.py::create_error_table_sql` 运行期幂等建、业务列全可空（不能
  `LIKE` 主表，否则 `amount NOT NULL` 会把脏行再拒一次）
- **发现**：T-SHADOW-01 步骤 1 探查（2026-10-16）
- **现象**：`infra/e2e/doris-init/01-init.sql:9-13` 注释写「质量分流：被拒行只在
  quarantine 表留**聚集计数 + 错误码**，**不落原始敏感值**（与 M-K 边界一致）」——
  与 **PRD 10.5 第 4 条「错误表例外条款」**（数据面未命中质量规则的数据分流写入错误表并
  **记录原始值与违规原因**，仅报告/证据/快照不回传明文）**直接矛盾**；
  PRD AC-02 亦确认「错误表按 10.5 例外条款保留原始值」
- **影响**：注释误导后续实现者以为错误表不该存原始值（PR-A 的错误表 DDL 正是按
  「保留原始值」实现）。**仅是注释错误**，init SQL 实际未建错误表，无行为影响
- **修复建议**：改注释为「错误表保留原始值与违规原因（PRD 10.5 #4 例外条款）；
  报告 / 证据 / 快照只回聚合，不回传错误表明文」，另开 docs PR
- **关联**：T-SHADOW-01（PR-A 的错误表实现依据）；PRD FR-44 / 10.5 #4 / AC-02

### T-SHADOW-03：未覆盖脏行类型 → batch 毒化风险（**Closed (Won't Fix)**）

- **状态**：**Closed (Won't Fix)**（2026-10-17，fix/final-gate，人类裁决不修直接关）
- **不修原因**：
  1. **已知毒化已被消除**：PR-A 的双路分流在路由层把演示的两条脏行（amount NULL /
     未来水位）挡在影子表之外，run24 实证 1000 + 2，**不存在**已知的整批回滚；
  2. **残余风险不可穷举**：本条担心的是「质量规则未覆盖的**未来**脏行类型」（类型不匹配 /
     超长 / 精度溢出 / 字符集），**无法用枚举规则的方法一次性消灭**——扩规则只是把
     未知往后推一格；
  3. **根治属引擎能力**：真正的行级拒收要靠 SeaTunnel 的 `Assert` / save-mode 或
     目标表列全可空 + 报告兜底，属引擎能力边界与数据模型变更，不是 M2 收尾能做的
     缺陷修复。
- **遗留口径**：规则覆盖面之外的数据问题仍会以「整批失败 + 无错误码」的形式出现，
  排障走 Doris 侧错误日志；**这不影响已演示的 AC-10/AC-16 结论**（seed 脏行类型在覆盖内）。
- **发现**：T-SHADOW-01 PR-A 收口（2026-10-16，人类裁决补充）
- **现状**：PR-A 的双路分流在**路由层**消除了当前已知的 batch 毒化——valid 分支的
  `where <全部规则通过>` 已把演示的两条脏行（amount NULL / 未来水位）排除在影子表之外，
  影子表只收干净行，NOT NULL 不会再打（run24 实证 1000 + 2，**无需** `batch_size=1`
  或逐行 try/catch）
- **残余风险**：毒化只是**被谓词挡住**，不是被消灭。将来若出现**质量规则未覆盖**的脏行
  类型（类型不匹配 / 超长 / 精度溢出 / 字符集不合规等），它仍会通过 valid 分支进入
  影子表 → 整批回滚，症状与 run20 相同
- **影响**：规则覆盖面之外的数据问题会以「整批失败」而非「单行分流」的形式出现，
  且**没有错误码**（不命中任何规则）→ 排障只能靠 Doris 侧错误日志
- **若将来要修的方向（本次未采纳）**：① 扩大规则覆盖面（枚举目标表各列的约束类型）；② 目标表列
  放宽为可空 + 由报告兜底；③ 引入 SeaTunnel 的 `Assert` / save-mode 能力做行级拒收
- **关联**：T-SHADOW-01（PR-A 已消除已知毒化）；T-DEMO-01（源端脏行口径）；
  T-METRICS-01；run20 的 1002→1000 教训

### T-SHADOW-04：etl_quality_summary 在 src/ 下零写入点（**Closed**）

- **状态**：**已修（fix/final-gate，取建议 ②）**——从 `infra/e2e/doris-init/01-init.sql`
  删除 `etl_quality_summary` 的建表定义（原 50-65 行），**只保留
  `DROP TABLE IF EXISTS etl_quality_summary;`** 用于清理历史环境残留的幽灵表
  （脚本仍幂等可重复执行）。表头注释改为「质量分流的聚合事实落控制面
  `quality_reports`，Doris 侧不建聚合表」，并写明删表原因
- **落点确认**：质量分流的聚合事实**唯一落点**是控制面 `quality_reports`
  （含 `rules` / `diagnostics` / 错误码统计），Doris 侧无消费者；删除后
  `SHOW TABLES FROM doris_dw` 只剩 `order_wide` / `order_wide_shadow`（+ 运行期
  由执行通道建的 `order_wide_error`）
- **发现**：T-SHADOW-01 步骤 1 探查（2026-10-16）
- **现象**：`etl_quality_summary` 只在 `infra/e2e/doris-init/01-init.sql:50-65` 建表，
  全仓 `src/` **零写入点**（`grep -rn etl_quality_summary src/` 无命中）→ 演示库里恒空表
- **影响**：无功能阻断——质量分流的**聚合事实**实际落在控制面 `quality_reports`
  （含 `rules` / `diagnostics` / 错误码统计），Doris 侧这张表目前无消费者。
  但它与 PRD 10.5 的口径（聚合可回传、明文不可回传）互为印证，留空会误导读者以为
  分流聚合走 Doris
- **处置（已落，取建议 ②）**：删除表定义 + 更新注释（见上「状态」）。**未取建议 ①**
  （让执行通道在终态写聚合行）——那会新增一条 Doris 写入链路与失败面，
  而聚合事实在控制面已完整，属重复建设。
- **关联**：T-SHADOW-01（分流聚合口径）；T-DOCS-01（同属 init SQL 口径问题）；
  PRD FR-44 / 10.5；`execution/routing.py`（聚合事实实际落点）

### T-CONSOLE-01：控制台刷新后显示态未持久化

- **状态**：**已修（chore/M2-cleanup）**
- **发现**：S10 场景 A 截图取证（2026-10-16，F5 刷新后顶部栏恒显「未登录」）
- **现象**：`frontend/src/stores/actor.ts` 的 `actorName` 初值硬编码 `'未登录'`
  且不从 localStorage 恢复（只有 `actorId` / `projectId` 恢复）→ 刷新后身份显示
  丢失（`AppShell.vue` 顶部栏）；`actorRole` 同样未持久化（刷新后角色徽标为空）
- **影响**：显示态不完整；鉴权**不受影响**（`X-Actor-ID` 由 `client.ts` 拦截器
  每次经 `getActorId()` 现读 localStorage）。截图/演示需手动 bind 才能显示身份
- **处置**：`actorName` 已由 `fix/S10-console-refresh-defects` 修复；`actorRole`
  由本 PR 补齐 —— `client.ts` 增 `ACTOR_ROLE_STORAGE_KEY` 读写，
  `actor.ts` 的 `actorRole` 初值改从 `getActorRole()` 恢复、`bind()` 落库、
  `clear()` 一并清除（职责来自 bootstrap 响应、不涉密，可随身份持久化）。
  鉴权本就未受影响（`X-Actor-ID` 由拦截器现读 localStorage）。
- **关联**：`fix/S10-console-refresh-defects`；`docs/demo/演示回放.md` §8.9

### T-TZ-01：引擎容器未设 TZ，与源库时区差 8h 导致水位谓词全量拒收

- **状态**：**已修（fix/infra-tz）**——`infra/docker-compose.yml` 给 `worker` /
  `seatunnel` / `doris-fe` / `doris-be` 补 `TZ: Asia/Shanghai`，与 `mysql` / `postgres` 对齐
- **发现**：S10 补 Studio 截图取证复跑（2026-10-17，run27）
- **根因**：质量契约的水位谓词 `updated_at <= CURRENT_TIMESTAMP`
  （`llm/mock_provider.py::_QUALITY_RULES`）由 **SeaTunnel JVM 运行时求值**，而
  `worker` / `seatunnel` / `doris` 容器未设 `TZ`（= UTC）、`mysql` / `postgres` 设了
  `TZ=Asia/Shanghai` → 源数据是 **CST 墙钟**、比较基准是 **UTC 墙钟**，恒差 8h
- **现象（run27）**：源 1002 行全被判「未来时间」→ `order_wide_error` 收 1002 行
  （`QUALITY_FUTURE_WATERMARK` 1001 + `QUALITY_NOT_NULL_AMOUNT` 1），`order_wide` /
  `order_wide_shadow` 全 0；运行仍报 `SUCCEEDED`（swap 照发，只是搬了 0 行）
- **为什么此前没暴露**：MySQL 数据卷持久化，种子数据是容器**首次 init** 时灌的，
  远早于 run26（CST 墙钟值恰好 < UTC-now）→ 侥幸通过。**只要重 init MySQL 让数据
  变新鲜（须比 CST-now 早 ≥8h 才过），任何 real-cli 跑都会全拒**
- **判定依据（逻辑闭环）**：最新数据行 `07:12:24 CST` 被拒 → 上界必 < `07:12:24`；
  而 CST-now `07:15` > `07:12:24`（本该放行）→ 上界不是 CST-now；UTC-now
  `23:15（前一天）` < `07:12:24` ✓
- **复验（run28）**：补 TZ 后重跑 → `SUCCEEDED` / 源 1002 / 目标 1000 /
  质量 `PASSED · 1002-1000-2 · 0.20%` / `order_wide=1000`、`shadow=0`、`error=2`
- **遗留**：Doris FE 的 `TZ` 已生效（`@@system_time_zone=Asia/Shanghai`），但 Doris
  **会话变量** `time_zone` 仍是 FE 内置默认 `Etc/UTC`（`fe.conf` 无该项），
  `SELECT NOW()` 仍回 UTC；本演示链路不消费 Doris 侧 `NOW()`，无功能影响；
  彻底对齐需 `SET GLOBAL time_zone`（运行态设置，未纳入本 PR）
- **关联**：`fix/infra-tz`；`infra/docker-compose.yml`；`llm/mock_provider.py:56-66`；
  run27 / run28 日志（`docs/stages/assets/S10-e2e/`）

### T-QUALITY-01：质量契约未声明阈值 → 拒绝率 100% 仍判 PASSED（fail-open）

- **状态**：**已修（fix/final-gate）**——契约侧显式声明 `max_reject_rate: 0.05`，
  判定侧语义未动（见下「修法边界」）
- **发现**：S10 补 Studio 截图取证复跑（2026-10-17，run27 暴露）
- **现象**：run27 拒绝率 **100%**（1002/1002），质量报告仍 `status=PASSED`，
  第 2 道发布门禁据此放行 → `execution.published` 事件照发、swap 真执行
  （Doris FE `fe.audit.log` 有 `ALTER TABLE order_wide REPLACE WITH TABLE
  order_wide_shadow PROPERTIES('swap'='true')`，`State=OK`）
- **根因（两层，缺一不可）**：
  - **第 1 层（直接原因，判定侧）**：`execution/routing.py::classify_quality`
    - `max_rows = contract.get("max_rejected_rows")` → `None`
    - `max_rate = contract.get("max_reject_rate")` → `None`
    - `over_rows` / `over_rate` 用 `isinstance(..., (int, float))` 判类型 → `None`
      直接短路为 `False` → 既不入 FAILED 也不入 WARNING → **落 `STATUS_PASSED`（第 126 行）**
    即：**阈值缺失 = 放行**（fail-open），且不区分「未声明」与「声明为 0」
  - **第 2 层（为什么另一套阈值没兜住，监督侧）**：系统里**其实有**阈值，但长在别处——
    监督域 `DEFAULT_SUPERVISION_CONTRACT`（`harness/prepare/service.py:37-45`，
    `max_reject_rate: 0.5`）与 `supervision/contracts.py:35/84` 都定义了
    `max_reject_rate`，却**从未参与质量判定**：
    ① `worker/tasks/consume_outbox.py:735` 调 `generate_quality_report(...)`
    **不传 `quarantined=`**（恒 `False`）；② `supervision_snapshot` 恒 `None`。
    **根本原因是 S7 没有产出监督事实源**（`execution/router.py` 详情端
    `supervision_snapshot` 硬编码 None，见 T-AUDIT-01「遗留观察」）——没有事实源就
    无从判隔离，也就传不出 `quarantined=True`。**把监督判决接到质量判定属 S8
    （监督域闭环）的接线范围**，不在 M2 收尾内；本条只修第 1 层。
- **契约侧确认**：演示契约由 `llm/mock_provider.py:126-145` 生成，此前只声明
  `valid_sink` / `error_sink` / `routing` / `traceability` / `predicates`，
  **不含任何阈值字段**；而 14 项门禁的「质量契约四要素」
  （`gate/rules/quality_contract.py` GATE-004~007 = sink / routing / traceability /
  predicates，FR-19）**本就不含阈值** → 无阈值仍 14/14 合规。
  **故这不是生成侧或门禁越界，而是判定侧的默认值口径问题**
- **修法（fix/final-gate，只动契约、不动判定语义）**：
  1. `llm/mock_provider.compliant_plan` 的 `quality_contract` **显式声明
     `max_reject_rate: 0.05`**（5%）；`tests/conftest.py` 的 `S6_TEST_PLAN`
     同口径声明，避免测试夹具走 fail-open；
  2. **修掉误导性测试**：`tests/worker/test_consume_outbox.py` 的
     `test_shadow_executor_factory_is_used_for_data_plane` 原用
     **100/1000 = 10% 拒绝率却断言 PASSED**，且注释写「拒绝率 10% 未超阈值」——
     这是 fail-open 的产物。现改为 **2/1000 = 0.2%** 干净样本（`sink_rows` 900→998），
     PASSED 是**真判出来的**，并补断言 `reject_rate ≈ 0.002`；
     `tests/helpers.py::FakeSeaTunnelClient` 的默认 metrics 同步改干净；
  3. **补回归**：`tests/llm/test_mock_provider.py` 断言生成契约必含 (0,1) 区间的
     `max_reject_rate`；`tests/execution/test_routing.py` 走**真实生成路径**取契约，
     断言 **3/1003 ≈ 0.30% 仍 PASSED**（取证数字不变）且 **60/1003 ≈ 5.98% FAILED**；
  4. **门禁四要素未动**（仍是 sink/routing/traceability/predicates）→ **不涉 AMEND**。
- **修法边界（必须说清）**：本次是**在生成侧把阈值声明出来**，`classify_quality`
  的「未声明 = 放行」语义**原样保留**。故对外部/未来契约仍可能 fail-open；
  要根治需给未声明阈值取保守默认（SPEC 冻结预算 `max_reject_rate=0.5`），
  那是**判定语义变更 → 需 AMEND**，不在本 PR。
- **影响**：数据面全量失败（下游一行没进）时系统仍判「质量通过」并发布空表，
  运行终态是 `SUCCEEDED` —— 对运维是**静默的成功**；生产后果比 run27 更重
  （**注**：本次修后，演示契约下已不再出现；判定侧兜底仍未做，见「修法边界」）
- **关联**：`execution/routing.py:113-126`、`execution/publish.py:163-169`、
  `worker/tasks/consume_outbox.py:735`、`llm/mock_provider.py:126-145`、
  `harness/prepare/service.py:37-45`、`supervision/contracts.py:35`；
  `docs/stages/assets/S10-e2e/demo-a-run27-20261017.log`

### T-CONSOLE-02：Pipeline Studio 多处演示态数据未标注（**Closed (Won't Fix)**）

- **状态**：**Closed (Won't Fix)**（2026-10-17，fix/final-gate，人类裁决「不修」）
- **不修原因（为什么不改代码）**：
  1. **门禁 `14 / 14` 是前端本地清单**：`DEFAULT_GATES` 是 SPEC 冻结的 14 项静态清单
     （结构校验项），页面展示它就是「清单全项通过」的语义，**不是伪造后端结果**；
     真要做成「取真实门禁结果」需后端补出参 + 落库回传（`freeze` 已收 `gate_results`
     入参但未回传），属**功能增量**，不是 M2 收尾的缺陷修复；
  2. **HOCON 正文 / 版本链 / 候选数**三处同理：缺的是后端出参与真实数据源，
     前端只能回落原型图口径的样例；改前端标记不解决「无数据源」本身；
  3. **已由文档覆盖**：`docs/demo/演示回放.md` §8.9 的「演示值注记」表逐项标注了
     哪些是演示值、哪些是真实数据，**截图取证的读者不会被误导**——这就是本项要
     防的唯一后果；
  4. **终极取证（§8.9）会再明确一次**：门禁 14/14 为前端本地清单、非后端结果。
- **遗留口径**：Pipeline Studio 的「门禁 14/14」**不代表本次运行通过了后端门禁校验**，
  仅代表前端静态清单全项展示；真实门禁结论以 API-05/06 与执行运行的门禁事件为准。
- **发现**：S10 补 Studio 截图取证（2026-10-17，A5 采集时逐项读码核对）
- **现象（同一页四处）**：
  1. **门禁 `14 / 14`**：`PipelineStudio.vue:77-92` 的 `DEFAULT_GATES`（14 项恒
     `passed: true`），`loadVersion()` 在 **L152 无条件** `gates.value = DEFAULT_GATES`
     —— 门禁清单**从未从后端取**，页面也不标「示意」
  2. **HOCON 正文**：`hoconText = currentVersion.value?.hocon ?? ''`（L70），而
     `GET /pipeline-versions/{id}` **不出参 `hocon`** → 恒空 → 回落 `SAMPLE_HOCON`（L336 / L447）
  3. **版本链 `V4`~`V6`**：`versionChain.length === 0` 时渲染 `chain__card--ghost`
     静态卡（L417-439），内容为原型图口径（「已回滚：凭据过期」等）
  4. **候选 `2 / 3`**：`Math.min(repairCount + 2, REPAIR_BUDGET)`（L96），`+2` 为原型图口径
- **影响**：控制台把演示值当真实治理事实展示 → 截图取证必须人工注记
  （见 `演示回放.md` §8.9「演示值注记」表）；`GateChecklist.vue:12-15` 的 `GateItem`
  只有 `{key,label,passed}`，**无 live / 示意 概念** → 无法在组件层标注
- **若将来要真修的方向（本次未采纳）**：① 后端补「版本门禁结果」出参（`freeze` 已收
  `gate_results` 入参，但未落库回传）→ 前端改真实取数；② 或统一给 `GateChecklist`
  加「示意」徽标，演示态一律标注（与 `SafetyGovernance.vue` 的 PDP 静态渲染口径一致）
- **关联**：`frontend/src/views/PipelineStudio.vue:70/77-92/96/152/336/417-439`、
  `frontend/src/components/GateChecklist.vue:12-15`；`docs/demo/演示回放.md` §8.9

### T-CONSOLE-03：Safety 页 Benchmark 六项门禁回落演示值，未标「示意」（**Closed，PR #27**）

- **状态**：**Closed（PR #27，fix/S10-console-contract-a）**——`gateRows` 不再回落
  fallback 实测值：`latestRun` 为空时**直接返回空数组**，模板显示「无评测运行」空态；
  页头的魔法兜底（`report_sha256 ?? '9b602'`）一并删除。演示值不再以「真实值」面貌出现。
  **本条此前状态行误留 Open，本次（fix/final-gate）修正为 Closed。**
- **死字段已清（fix/final-gate）**：`gateRows` 曾算出 `live: Boolean(found)` 但
  **模板从未消费**——`latestRun` 为空即返回 `[]` 后，`found` 恒存在、`live` 恒 `true`，
  该字段已完全无意义，本次删除。
- **发现**：S10 补 Studio 截图取证（2026-10-17，A4 观察点复核时读码确认）
- **现象**：`SafetyGovernance.vue:65-88` 的 `gateRows` 在 `latestRun` 为空时回落到
  fallback 实测值（1.00 / 0.97 / 0.02 / 1.00 / 1.00 / 0.05）且 `passed: true`；
  本机 `GET /projects/{id}/benchmark-runs` 实测为空 `{runs: [], total: 0}` → 页面恒显
  **`6 / 6 PASS`**。页头 `report_sha256` 亦有硬编码兜底（`?? '9b602'`）
- **关键点（历史）**：`gateRows` 曾算出 `live: Boolean(found)`（L85），但**模板从未使用
  该字段**（表格列只有 门禁指标 / 阈值 / 实测 / 结论）→ 演示值与真实值在视觉上不可区分。
  PR #27 改成「无运行即空态」后 `live` 彻底无意义，**该字段已在 fix/final-gate 删除**。
- **影响**：与 T-CONSOLE-02 同类；A4 截图里「Benchmark 6/6 · 证据覆盖率 100%」是演示值，
  而同屏「证据时间线 7 事件」是真的 —— 二者混排易被误读为同一来源
  （**注**：PR #27 修后已不出现演示值；A4 旧截图的该现象属修复前画面）
- **修复（已落）**：不引入 `live` 徽标，而是**彻底去掉演示回落**——无运行数据即空态，
  视觉上不存在「演示值冒充真实值」的可能（比加徽标更彻底）
- **关联**：`frontend/src/views/SafetyGovernance.vue:65-88/196-235/308`；
  `docs/demo/演示回放.md` §8.9

### T-CONSOLE-04：Studio 读 `version_no`，后端回 `version_number` → 渲染 `Vundefined` / `VNaN`（**已修，PR #25**）

- **状态**：**已修（`fix/S10-console-version-no` / PR #25）** —— 前端统一改读
  `version_number`（`types.ts` 类型 + `PipelineStudio.vue` 3 处 + `VersionDiff.vue` 2 处，
  共 6 处 / 3 文件）；`vue-tsc` / `eslint` / `vite build` 全绿，playwright 实跑确认
  `/studio` 不再出现 `Vundefined` / `VNaN`（Diff 标题 = `V1（VALIDATED）→ V2（DRAFT）`）。
  遗留 `?? 7` 魔法兜底归 T-CONSOLE-02。
- **发现**：S10 补 Studio 截图取证（2026-10-17，A5 画面出现 `VNaN` 后读码定位）
- **现象（A5 截图可见）**：
  - Diff 标题渲染为 **`Vundefined（VALIDATED）→ VNaN（DRAFT）`**
    （`V${undefined}` / `V${undefined + 1}`，`PipelineStudio.vue:373-374`）
  - 版本链首卡恒显 **`V7`**（`currentVersion?.version_no ?? 7`，L411）
- **根因**：前端 `PipelineVersionView` 声明 `version_no: number`（`types.ts:135`），
  而 `GET /pipeline-versions/{id}` 实际出参是 **`version_number`**（run28 curl 实测 +
  `pipeline_versions` 表列名）→ 取到 `undefined`
- **同源问题**：该端点也不出参 `hocon` / `etl_plan`（前端类型声明有），
  故 HOCON 视图恒回落 `SAMPLE_HOCON`（已并入 T-CONSOLE-02 第 2 项）
- **影响**：取证画面带明显 bug 文案；但**不影响**版本 `status=VALIDATED` 与
  `artifact_sha256` 的真实性（二者字段名正确、值来自后端）
- **修复方向**（待裁决）：前端统一改读 `version_number`（1 处类型 + 2 处引用），
  或后端出参加 `version_no` 别名；**选后端方案即涉接口字段口径 → 需 AMEND**
- **关联**：`frontend/src/views/PipelineStudio.vue:373-374/411`、
  `frontend/src/api/types.ts:132-143`；`docs/demo/演示回放.md` §8.9「缺陷详情备查 2」

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
