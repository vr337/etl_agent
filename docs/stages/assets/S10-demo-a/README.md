# S10 场景 A 演示截图（6 张：A0~A4 = run17，A5 = run28）

用途：`docs/demo/演示回放.md` 第 8 节「场景 A 主链路演示」的前端对照截图。
生成时点：13 步跑到 STEP 12（执行 + 质量报告）之后——执行 / 质量 / 审批 /
证据均已落库。截图期间请勿清库或跑全量回归。

**数据来源分两轮**（核对明细见 `演示回放.md` §8.9）：

| 截图 | 轮次 | 日期 | 引擎口径 |
| --- | --- | --- | --- |
| `A0` ~ `A4` | run17（`fix/S10-demo-coverage`） | 2026-10-16 | mock 引擎，Doris 目标表 0 行 |
| `A5` | run28（`fix/infra-tz` / PR #23 合并后） | 2026-10-17 | **real-cli 真引擎**，Doris `order_wide=1000` |

| 文件 | 视图 | URL | 观察点 |
| --- | --- | --- | --- |
| `A0-dashboard.png` | Dashboard 总览 | `/dashboard` | 真实计数（1 项目 / 2 连接 / 2 Profile / 1 执行 / 1 质量） |
| `A1-datasources.png` | 数据源与资产 | `/datasources` | 2 连接（mysql_orders / doris_dw）· 2 Profile · 文件资产空态 |
| `A2-runtime-execution.png` | 运行中心 · 执行详情 | `/runtime` | `SUCCEEDED` · 源 1002 / 目标 1000 |
| `A3-runtime-quality.png` | 运行中心 · 质量报告 | `/runtime` | `PASSED` · 拒绝率 0.20% · 错误码分布 |
| `A4-safety-benchmark.png` | 安全治理与 Benchmark | `/safety` | **证据时间线 7 事件** · 页脚 `valid=true` |
| **`A5-pipeline-studio.png`** | Pipeline Studio | `/studio` | 版本 **`VALIDATED`** · `artifact_sha256 9546ea356749…` · 版本链 · 版本 Diff 区块 |

## 演示值注记（勿当取证事实引用）

| 截图 | 演示值 | 来源 | 票号 |
| --- | --- | --- | --- |
| `A4` | Benchmark 六项门禁 `6 / 6 PASS` 与「实测」列、页头 `report_sha256` 兜底值 | `SafetyGovernance.vue:65-88`（`benchmark-runs` 为空时回落冻结清单口径演示值） | **T-CONSOLE-03** |
| `A5` | 门禁 `14 / 14` 清单 | `PipelineStudio.vue:77-92` `DEFAULT_GATES`（无条件赋值，非后端结果） | **T-CONSOLE-02** |
| `A5` | HOCON 正文（回落 `SAMPLE_HOCON`）、版本链 `V4`~`V6` 静态卡、候选 `2 / 3` 徽标 | 同页演示态兜底 | 同 **T-CONSOLE-02** |

## 已知画面缺陷（如实保留，未修饰）

- `A5`：Diff 标题渲染为 **`Vundefined（VALIDATED）→ VNaN（DRAFT）`**、版本链卡恒显 `V7`
  —— 前端读 `version_no`，而后端该端点回的是 `version_number`（**T-CONSOLE-04**）。

## 数据口径

- **A0~A4（run17）**：mock 引擎（`ETL_AGENT_ENGINE_MODE=mock`），Doris 目标表保持 0 行
  （不搬数据）；质量指标为与 seed 形态一致的演示态合成值（1002/1000/2）；
  证据 7 事件 = PDP + PREPARE + **APPROVAL_DECISION** + COMMIT + EXECUTION_TRANSITION×3。
- **A5（run28）**：real-cli 真引擎，Doris `order_wide=1000` / `order_wide_shadow=0` /
  `order_wide_error=2`；质量 `PASSED · 1002/1000/2 · 0.20%`；证据 `valid=true · event_count=7`。

前端地址：**`http://localhost:5173`**（Vite 绑 IPv6 回环 `[::1]`，`127.0.0.1:5173` 连不上）。
绑定：`localStorage.setItem('actor_id','admin@demo.local')` + `project_id`；项目已存在时
**不要**走 Bootstrap 表单（会 409）。

缺陷详情与修法见 `演示回放.md` §8.9 的「缺陷详情备查 1 / 2」。
