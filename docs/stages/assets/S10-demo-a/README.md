# S10 场景 A 演示截图（6 张，均来自 run30 终极真实取证）

用途：`docs/demo/演示回放.md` 第 8 节「场景 A 主链路演示」的前端对照截图。
**6 张全部来自同一次运行 run30**（2026-10-17），不再存在跨轮次拼图。

生成时点：13 步跑到 STEP 11（执行 + 质量报告）之后——执行 / 质量 / 审批 / 证据均已落库。
截图期间请勿清库或跑全量回归。

## 取证环境

| 项 | 值 |
| --- | --- |
| 运行 | run30 · `ExecutionRun 293f1947-de38-4692-87f0-cbe01c2b4afa` |
| 项目 | `c4a19076-c147-452d-b473-d5440dd61b88`（`admin@demo.local` / ADMIN） |
| 引擎 | `ETL_AGENT_ENGINE_MODE=real-cli`（SeaTunnel CLI 提交 + REST 查询） |
| 代码基线 | master `a366dc4`（PR #31 合并后） |
| 执行结果 | `SUCCEEDED` · 源 **1003** / 目标 **1001** / 拒绝 **2** · **0.20%** · `PASSED` |
| Doris | `order_wide=1001` / `order_wide_shadow=0`（Swap 后清空）/ `order_wide_error=2` |
| 证据账本 | `valid=true · event_count=8` |

> **「3 条脏数据」的口径修正**：seed 的 3 条「脏」中，400 字符超长地址那条在 **T-DEMO-01**
> （`address` 放宽至 `VARCHAR(500)`）后已可正常落库；真实被拒 **2 条**
> （`QUALITY_NOT_NULL_AMOUNT` ×1 + `QUALITY_FUTURE_WATERMARK` ×1）。故目标表 1001 行。

## 截图清单

| 文件 | 视图 | URL | 观察点 |
| --- | --- | --- | --- |
| `A0-dashboard.png` | Dashboard 总览 | `/dashboard` | 身份 `admin@demo.local` · 最近执行 `SUCCEEDED` 1K→1K · 活跃 Pipeline 1 · 成功率 100.0% · 证据账本 `VALID` |
| `A1-datasources.png` | 数据源与资产 | `/datasources` | 2 连接（`mysql_orders` READY / `doris_dw` UNTESTED）· 凭据托管 2/2 · 双 Profile · 文件资产空态 |
| `A2-runtime-execution.png` | 运行中心 · 执行 + 审批 + 质量 | `/runtime` | `293f1947` `SUCCEEDED` · 准备单 `PREP-2a13` `APPROVED` · 发布 `PUBLISHED` · 质量 `PASSED` 1,003/1,001/2 · 0.20% |
| `A3-runtime-quality.png` | 运行中心 · 质量 + 监督（同页下滚位） | `/runtime` | 质量报告 1,003 / 1,001 / 2 / **0.20%** · `PASSED` · 发布 `PUBLISHED` · 监督动作 `CONTINUE` |
| `A4-safety-benchmark.png` | 安全治理与 Benchmark | `/safety` | **证据时间线 8 事件** · 页脚 `valid=true · event_count=8` · Benchmark 门禁 **`0 / 6`**（真实空态） |
| `A5-pipeline-studio.png` | Pipeline Studio | `/studio` | 版本 **`V1 · VALIDATED`** · `artifact_sha256 9546ea356749…` · 版本 Diff 区块 · HOCON 区为真实缺参说明 |

> A2 / A3 为同一页面 `/runtime` 的两个滚动位置，内容重叠属页面结构使然，非重复采集。
>
> A1 的 `doris_dw` 显示 `UNTESTED` 属**预期**：Doris 连接器无探针实装，
> `POST /connections/{id}/test` 对 DORIS 返回 `422 BIZ_422_CONNECTOR_UNSUPPORTED` 是策略判决；
> Doris 连通性由 SINK Profile 探查与真实落数 1001 行证明。

## ⚠️ 演示值注记（勿当取证事实引用）

**最重要一条 —— Studio 的「门禁 14/14」不是后端门禁结果**：

- `A5` 右侧 `确定性门禁 14 / 14` 来自 `PipelineStudio.vue` 的 `DEFAULT_GATES`（14 项恒
  `passed: true`），`loadVersion()` 中**无条件赋值**，**从未从后端取数**；
- 它是 SPEC 冻结的 14 项确定性门禁**清单**在页面上的静态呈现，**不代表**本次运行真实通过了
  后端门禁校验；真实门禁结论以 API-05/06 生成响应与执行门禁事件为准；
- 票号 **T-CONSOLE-02**（`Closed (Won't Fix)`，后果由 `演示回放.md` §8.9 注记覆盖）。

其余演示值：

| 截图 | 演示值 | 来源 | 票号 |
| --- | --- | --- | --- |
| `A0` | 指标卡副标题「较昨日 +3 · 3 个 Pipeline」「失败 3 次」「已就绪版本 48 个」 | `Dashboard.vue:176/179/183` 的 `hint` 硬编码（**卡内主数值是真的**） | T-CONSOLE-02 同类 |
| `A0` / `A2` | 「Capability 签发与消费 47 / 47 / 1」「今日令牌签发 47 · 已消费 47 · 重放拦截 1」 | `Dashboard.vue:269-270`、`RuntimeCenter.vue:192` 硬编码（已带「示意」徽标） | T-CONSOLE-02 同类 |
| `A0` | 「PDP 策略分布 · 今日」「副作用调用 · 今日」两组计数 | 硬编码（已带「示意」徽标） | 同 T-CONSOLE-02 口径 |
| `A5` | 门禁 `14 / 14` 清单 | 见上方专项说明 | **T-CONSOLE-02** |
| `A5` | 版本链 `V4`~`V6` 静态卡、候选 `2 / 3` 徽标 | `chain__card--ghost` 静态卡 / `repairCount + 2`（原型图口径）。本次仅 1 个版本，版本链只显真实 `V1` | 同 T-CONSOLE-02 |

> `A4` 的 Benchmark **已不再是演示值**：PR #27（T-CONSOLE-03）删除兜底后改为真实空态 `0 / 6`。
> 旧截图（run17）的「6 / 6 PASS」属修复前画面，不再引用。

## 重截记录

- 2026-10-17 **run30 终极取证**：6 张全部重截（此前 A0~A4 来自 run17 mock 口径、A5 来自 run28），
  统一到同一次 real-cli 真实运行，消除「数据来源不一致」。
- 更早：`A5` 曾于 **PR #25**（T-CONSOLE-04 版本号字段名）修复后按 run28 重截一次。

## 数据口径（curl 核对）

- 执行：`GET /executions/293f1947…` → `SUCCEEDED` / `source_rows=1003` / `sink_rows=1001` / `FINISHED`
- 质量：`GET /executions/293f1947…/quality-report` → `PASSED` / 1003-1001-2 / `reject_rate=0.001994…`
- 版本：`GET /pipeline-versions/6a46f452…` → `VALIDATED` / `version_number=1` /
  `artifact_sha256=9546ea356749e03f752b53b1e007c71033df9c3e264616cbe601c75bbbef803f`
- 证据：`GET /projects/{pid}/evidence-stream/verify` → `valid=true` / `event_count=8` /
  `head_hash=1fadb5c64c93a2aa9aa073f45437333c2923fbd8ad74c2bb7804cba2a234d242`
- 事件序列：`PDP_DECISION` → `PREPARE_CREATED` → `APPROVAL_DECISION` → `COMMIT_ISSUED` →
  `EXECUTION_TRANSITION` ×3 → `SUPERVISION_ACTION`
- Doris 实查：`order_wide=1001` / `order_wide_shadow=0` / `order_wide_error=2`
  （`QUALITY_NOT_NULL_AMOUNT:1` + `QUALITY_FUTURE_WATERMARK:1`）

## 复现方式

前端地址：**`http://localhost:5173`**（Vite 绑 IPv6 回环 `[::1]`，`127.0.0.1:5173` 连不上）。

```js
localStorage.setItem('actor_id','admin@demo.local');
localStorage.setItem('project_id','c4a19076-c147-452d-b473-d5440dd61b88');
localStorage.setItem('actor_role','ADMIN');
location.reload();
```

> 项目已存在时**不要**走 Bootstrap 表单（会 409）。
>
> 后端容器化后，`infra/e2e/demo-scenario-a.ps1` 的连接地址需用 compose 服务名
> （`mysql:3306` / `doris-fe:9030`）；脚本默认的 `127.0.0.1:13306/9030` 是「后端跑在宿主机」
> 视角。另：Doris `/test` 的 422 属预期，需带 `-StopOnError:$false` 运行。

缺陷详情与修法见 `演示回放.md` §8.9 的「缺陷详情备查 1」（版本 id 404，PR #15 已修）。
