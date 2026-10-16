# 端到端演示（T-22 / AC-10）

> SPEC 依据：`etl_agent-SPEC_v1.0.3-Baseline` 第 7.2 章（端到端演示链路）
> 覆盖 FR：FR-01 / FR-16~18 / FR-31~33 / FR-36 / FR-38~39 / FR-43~46 / FR-50~52
> 对应 AC：**AC-10**（端到端演示链路）、AC-01 / AC-04 / AC-05 / AC-07 / AC-08 / AC-09 / AC-13

本目录给出**两条可复现的演示链路**，以及支撑它们的基础设施初始化脚本。演示以真实 HTTP 调用驱动后端，
控制台前端（`frontend/`）与脚本观察的是**同一份治理事实**——脚本打印什么，控制台就显示什么。

---

## 一条重要口径：引擎为 mock 模式

S10 的人类裁决为 **C：mock engine 模式**。该模式**通过 `ETL_AGENT_ENGINE_MODE=mock`
环境变量显式开启**（`config.py` 白名单 real/mock，缺省 `real`；`ENV=prod` 禁止 mock，
启动 fail-fast）。含义是：

- **被 mock 的范围**：引擎侧两个交互——`--check` 配置校验与作业提交
  （`MockSeaTunnelClient`：`check` 直接通过、`submit` 返回稳定 job_id、`job_status`
  恒 `FINISHED` 并回传演示态聚合指标；指标与真实 seed 的 1002 行 / 2 脏行同形态，
  **mock 不搬数据**，Doris 表不会被写入）；
- **其余全部真实执行**：后端 API、身份与授权、HOCON 生成、14 项确定性门禁、四眼审批、
  单次 Capability 签发、质量契约判定、证据账本哈希链（七类审计钩子均接线）。

因此本演示**不是**「点几下看看界面」的演示——每条链路都留下可验证的证据（哈希链 + 门禁结果 + 质量报告）。
脚本 STEP 10 的 `[MOCK]` 标注与会话环境变量 `ETL_AGENT_ENGINE_MODE` **绑定**（不是硬编码字符串）：
置 `mock` 才打 `[MOCK]`，置 `real` 改打 `[OK] engine_mode=real`。worker 启动日志同样打印
实际模式（可审、可关）。

> 历史口径修正（2026-10-16）：S10 交付时「mock 引擎」只写在脚本注释里、生产 worker 并无
> mock 接线（硬编码 REST 客户端），本次 fix 补齐——详见 `演示回放.md`「场景 A 完整演示」节。

---

## 前置准备

### 1. 启动基础设施

```powershell
# 最小集合：PostgreSQL + Redis + 控制面 API
docker compose -f infra/docker-compose.yml --profile app up -d

# 追加执行层：Celery Worker / Beat + SeaTunnel Zeta
docker compose -f infra/docker-compose.yml --profile exec up -d

# 追加端到端演示所需：MySQL（源）+ Doris（目标）
docker compose -f infra/docker-compose.yml --profile e2e up -d
```

> **注意**：`infra/docker-compose.yml` 已内置 `e2e` profile（MySQL + Doris FE/BE，
> 见 `chore/S10-e2e-deps`）；宿主机端口为 MySQL `13306`（3306 被 WSL relay 占用）、
> Doris `9030`。脚本默认按 `127.0.0.1` + 发布端口寻址，无需改脚本。

### 2. 演示数据面（MySQL 源 / Doris 目标）

已由 compose `e2e` profile 内置（`chore/S10-e2e-deps`），无需手工补服务定义：

| 服务 | 镜像 | 宿主机端口 | 说明 |
| --- | --- | --- | --- |
| `mysql` | `mysql:8.4` | `13306→3306` | 3306 被本机 WSL relay 占用，故改发 13306 |
| `doris-fe` / `doris-be` | `apache/doris:{fe,be}-3.0.8` | `9030`（query）/ `8030`（HTTP） | FE 讲 MySQL 协议，source 探查与 sink 写入同端口 |

初始化 SQL 已就绪，容器首次启动自动执行：

| 文件 | 作用 |
| --- | --- |
| `infra/e2e/mysql-init/01-seed.sql` | 建 `source_db.orders`，灌 1,000 行干净数据 + 2 行故意脏数据（NULL 金额、未来水位；第 3 行脏数据因源列宽报错不可落库，见 T-DEMO-01，实际落库 1,002 行） |
| `infra/e2e/doris-init/01-init.sql` | 建 `doris_dw.order_wide`（唯一键）+ `order_wide_shadow` + `etl_quality_summary`（幂等：DROP IF EXISTS + CREATE，可重复执行） |

> 为什么种子数据里要有脏数据？因为演示的价值一半在「错误分流」——
> 拒绝率非零但可控（2 / 1002），质量报告才会呈现真实的四态而非永远 PASSED。

### 3. 后端环境

```powershell
# 后端在 8000 端口
curl http://127.0.0.1:8000/health

# 若后端开启了 bootstrap token 校验，导出它（不要写进脚本、不要提交）
$env:ETL_AGENT_BOOTSTRAP_TOKEN = "<your-token>"
```

### 4. 前端控制台（可选，用于对照）

```powershell
cd frontend
npm install
npm run dev          # → http://127.0.0.1:5173
```

Vite 已配置 `/api` 与 `/health` 代理到 `http://127.0.0.1:8000`，无需额外跨域设置。

---

## 场景 A：正常链路（生成 → 门禁 → 审批 → 提交 → 质量报告）

```powershell
pwsh -File infra/e2e/demo-scenario-a.ps1
```

脚本按 12 步推进，每步打印事实：

| 步骤 | 动作 | 观察点 | 对应 AC |
| --- | --- | --- | --- |
| 00 | `GET /health` | 后端在线、Worker 摘要 | — |
| 01 | `POST /bootstrap` | `project_id` / `admin_user_id`（幂等：409 可重入） | — |
| 02 | `POST /connections` ×2 | 凭据**只传不回显**，响应零明文 | AC-02 |
| 03 | `POST /connections/{id}/test` | 连通性状态回写 | — |
| 04 | `POST /agent-runs` | AgentRun 创建，可能返回 `WAITING_INPUT` | AC-03 |
| 05 | `POST /agent-runs/{id}/messages` | **同 `thread_id` 复用**（不是新起线程） | **AC-03** |
| 06 | `GET /pipeline-versions/{id}` | EtlPlan / HOCON 候选 + `artifact_sha256` | — |
| 07 | `POST /pipeline-versions/{id}/freeze` | 14 项门禁全 PASS 才冻结 | **AC-09** |
| 08 | `POST /preparations` | 风险等级 / 需批准人数 / 影响报告 | AC-05 |
| 09 | `POST /preparations/{id}/decisions` ×2 | 四眼双人批准，**换身份**（禁自批） | **AC-01** |
| 10 | `POST /preparations/{id}/commit` | Ed25519 单次 Capability，TTL 300 s；`[MOCK]` 提交 | **AC-05** |
| 11 | `GET /executions/{id}` + `/quality-report` | 监督动作、四态质量报告、错误码分布 | **AC-07** |
| 12 | `GET /evidence-stream/verify` | `valid=true` + `head_hash` | **AC-09** |

> **AC 覆盖口径（run15 验收实测，详见 `演示回放.md` 第 8.4 节）**：本轮 PDP 判 P3、
> 四眼审批未激活， AC-01/AC-03 **未覆盖**、AC-05 **部分**（commit 已走）；AC-04 全覆盖
> （门禁 14/14）；AC-06/07/10 为 mock 口径下的部分覆盖（引擎合成指标、Swap 不执行）；
> AC-09（Benchmark 证据覆盖率）为独立任务。**AC-01/AC-03/AC-05 的完整覆盖见后续
> fix/S10-demo-coverage PR。**

---

## 场景 B：安全拦截、失败诊断与受管回滚

```powershell
pwsh -File infra/e2e/demo-scenario-b.ps1 -ProjectId <场景A得到的 project_id>
```

> 场景 B 演示的是系统**拒绝**的能力，比演示成功更能说明问题。

| 步骤 | 动作 | 期望结果 | 对应 AC |
| --- | --- | --- | --- |
| 02 | 跨项目写入请求 | 硬阻断（4xx），**副作用发生前**拦截 | AC-01 |
| 03 | P0 副作用准备单 | 进入 `WAITING_APPROVAL`（挂起而非放行） | AC-05 |
| 04 | 以发起人身份自批 | **被拒绝**（DB 双唯一约束） | **AC-01** |
| 05 | 读监督事实 | `THROTTLE`/`QUARANTINE` 命中，阻断发布与 Swap | **AC-08** |
| 06 | 读失败诊断 | 分类 / 阶段 / 可重试性；不可重试则禁自动重放 | AC-08 |
| 07 | 发起受管回滚 | 走 Harness 同协议（不是「直接删」） | AC-08 |
| 08 | 提案越级发布 | HTTP **409**（状态机不可跳跃） | **AC-13** |
| 09 | 读证据事件流 | 拒绝/监督/回滚**同样入链**；哈希链 `valid=true` | AC-09 |

---

## 脚本使用说明

两个脚本都支持：

```powershell
# 指定后端地址与身份
pwsh -File infra/e2e/demo-scenario-a.ps1 -ApiBase http://127.0.0.1:8000

# 不因单步失败而中止（便于一次看完全貌）
pwsh -File infra/e2e/demo-scenario-a.ps1 -StopOnError:$false
```

输出约定（便于现场讲解与日志检索）：

| 前缀 | 含义 |
| --- | --- |
| `[OK]` | 该步达到预期 |
| `[INFO]` | 中性事实（值、口径、说明） |
| `[MOCK]` | 该步为 mock（引擎口径） |
| `[BLOCK]` | **预期内的拒绝**（治理边界生效——这是好事） |
| `[WARN]` | 非预期但可继续 |
| `[FAIL]` | 未达预期，脚本汇总时会列出 |

退出时打印汇总与本次演示覆盖的 AC 清单。

---

## 身份注入说明（AC-11）

脚本与前端都以 `X-Actor-ID` 头注入身份，这**仅在非生产环境**成立：

| 环境 | 身份来源 | 前端行为 |
| --- | --- | --- |
| 开发 / 演示 | `X-Actor-ID`（脚本显式传；前端从 `localStorage` 注入） | 允许（`VITE_ALLOW_ACTOR_HEADER !== 'false'`） |
| 生产 | trusted-gateway 注入的 HMAC 签名身份（FR-65） | **一律不注入**（`import.meta.env.PROD` 恒为 false 分支） |

生产环境直传 `X-Actor-ID` 等于伪造身份，故前端在构建期就阻断（见 `frontend/src/api/client.ts` 文件头红线）。

---

## 排查

| 症状 | 可能原因 | 处理 |
| --- | --- | --- |
| `后端不可达` | API 容器未起 / 端口占用 | `docker compose ps` 确认 `etl_agent_api` 健康 |
| bootstrap 返回 403 | Token 不匹配 | 核对 `ETL_AGENT_BOOTSTRAP_TOKEN` 与后端环境变量 |
| bootstrap 返回 409 | 工作区已存在（幂等设计） | 按提示输入已有 `project_id` 继续 |
| 执行一直 `QUEUED` | Worker 未起 | `--profile exec` 启动 worker / beat |
| 门禁未全过 | 契约缺失（连接器/质量/增量等） | 读 `freeze` 响应中的失败项；门禁**不可放宽** |
| 证据校验 `valid=false` | 账本被外部改动 | 这正是设计目的：篡改必然被发现，不应继续演示 |
| `git checkout` 后 benchmark 校验失败 | CRLF 转换破坏了冻结制品 | 见仓库根 `.gitattributes`（`benchmarks/** -text`） |

---

## 相关文件

- `infra/e2e/mysql-init/01-seed.sql` — MySQL 源端种子（含脏数据）
- `infra/e2e/doris-init/01-init.sql` — Doris 目标端（唯一键 + 影子表 + 质量表）
- `infra/e2e/demo-scenario-a.ps1` — 场景 A（正常链路）
- `infra/e2e/demo-scenario-b.ps1` — 场景 B（拦截 / 诊断 / 回滚）
- `frontend/` — 控制台（5 大视图 + 版本 Diff + 初始化）
- `docs/stages/S10-前端控制台与端到端演示-阶段报告.md` — 阶段报告（含 DoD 自检与 UI 还原对照）
