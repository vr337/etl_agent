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
| `infra/e2e/mysql-init/01-seed.sql` | 建 `source_db.orders`，灌 1,000 行干净数据 + 3 行故意脏数据（NULL 金额、未来水位、400 字符超长地址）。源表 `address` 已加宽至 VARCHAR(500)（fix/T-COMMIT-01 / T-DEMO-01），**三条脏行全部落库，实际 1003 行** |
| `infra/e2e/doris-init/01-init.sql` | 建 `doris_dw.order_wide`（唯一键）+ `order_wide_shadow`（幂等：DROP IF EXISTS + CREATE，可重复执行）。错误表 `order_wide_error` 由执行通道运行期幂等建（业务列全可空），不在此预建。质量分流的聚合事实落控制面 `quality_reports`，Doris 侧不再建 `etl_quality_summary`（T-SHADOW-04 已删表定义，脚本仅留 `DROP` 清理历史残留） |

> 为什么种子数据里要有脏数据？因为演示的价值一半在「错误分流」——
> 拒绝率非零但可控（3 / 1003 ≈ 0.30%），质量报告才会呈现真实的四态而非永远 PASSED。
>
> ⚠️ **mock 引擎口径未同步**：`MockSeaTunnelClient.demo_metrics()` 仍硬编码
> `1002/1000/2`，故 `ETL_AGENT_ENGINE_MODE=mock` 下的演示态指标与真跑
> （1003/1000/3）不一致 —— 待独立小修（不属本次范围）。

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
npm run dev          # → http://localhost:5173
```

> **地址口径**：Vite 默认把 dev server 绑在 **IPv6 回环 `[::1]`**，故浏览器必须用
> `http://localhost:5173`；写 `http://127.0.0.1:5173`（IPv4）会连不上。
> （后端仍是 `http://127.0.0.1:8000`，Vite 的 `/api` 代理不受影响。）

Vite 已配置 `/api` 与 `/health` 代理到 `http://127.0.0.1:8000`，无需额外跨域设置。

---

## 场景 A：正常链路（生成 → 门禁 → 审批 → 提交 → 质量报告）

```powershell
pwsh -File infra/e2e/demo-scenario-a.ps1
```

脚本按 **13 步**推进，每步打印事实（run17 起「澄清恢复」独立成步，原 12 步版本见 `演示回放.md` §8.3）：

| 步 | 动作 | 观察点 | 对应 AC |
| --- | --- | --- | --- |
| 01 | `GET /health` | 后端在线、Worker 摘要 | — |
| 02 | `POST /bootstrap` | `project_id` / actor subject（幂等：409 可重入）；审批/操作员环境预备 | — |
| 03 | `POST /connections` ×2 + `POST /profiles` ×2 | 凭据**只传不回显**；双 Profile 探查（Doris 走 MySQL 协议通道） | AC-02 |
| 04 | `POST /connections/{id}/test` | mysql 受理 TESTING；Doris 422 为预期（无探针实装） | — |
| 05 | `POST /agent-runs`（**只传 requirement**） | 缺参 → `WAITING_INPUT` + `pending_question` | **AC-03** |
| 06 | `POST /agent-runs/{id}/messages` ×2 | 两步补参（来源端→目标端）→ 收敛；**同 `thread_id` 复用** | **AC-03** |
| 07 | `GET /pipeline-versions/{id}` | EtlPlan / HOCON 候选 + `artifact_sha256` | — |
| 08 | （版本冻结）14 项门禁 | 全 PASS 才冻结（`VALIDATED`） | **AC-09** |
| 09 | `POST /preparations`（含 `budget` 超限） | 命中 P1 → `risk=P1 · required_approvals=1 · required_roles=[SECURITY_APPROVER]` | AC-05 |
| 10 | `POST /preparations/{id}/decisions`（按 required_roles） | 换身份审批（**禁自批**）→ `READY` | **AC-01** |
| 11 | `POST /preparations/{id}/commit` | Ed25519 单次 Capability，TTL 300 s；`[MOCK]` 提交 | **AC-05** |
| 12 | `GET /executions/{id}` + `/quality-report` | 执行终态（轮询）、四态质量报告、落表 | **AC-07** |
| 13 | `GET /evidence-stream/verify` | `valid=true` + `event_count` + `head_hash` | **AC-09** |

> **AC 覆盖口径（run17 验收实测，详见 `演示回放.md` 第 8.4 / 9 节）**：
> AC-04 ✅（门禁 14/14）；**AC-03 ✅ / AC-05 ✅（run17 补齐**：缺参→澄清→两步同线程恢复；
> P1 预算超限→审批激活→commit 全链路）；**AC-01 ⚠️ 部分**（run17 补齐审批激活 + 禁自批 +
> 槽位唯一 + 票数达标；「双人不同槽」子项仅 P0 可达，由场景 B 覆盖）；
> AC-06/07/10 为 mock 口径下的部分覆盖（引擎合成指标、Swap 不执行）；AC-09 为独立任务。

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
