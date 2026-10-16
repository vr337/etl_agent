# S10 场景 A 演示截图（与 run17 对齐）

用途：`docs/demo/演示回放.md` 第 8 节「场景 A 主链路演示」的前端对照截图。
生成时点：13 步跑到 STEP 12（执行 + 质量报告）之后——执行 / 质量 / 审批 /
证据均已落库。截图期间请勿清库或跑全量回归。

| 文件 | 视图 | URL | 观察点 |
| --- | --- | --- | --- |
| `A0-dashboard.png` | Dashboard 总览 | `/dashboard` | 真实计数（1 项目 / 2 连接 / 2 Profile / 1 执行 / 1 质量） |
| `A1-datasources.png` | 数据源与资产 | `/datasources` | 2 连接（mysql_orders / doris_dw）· 2 Profile · 文件资产空态 |
| `A2-runtime-execution.png` | 运行中心 · 执行详情 | `/runtime` | `SUCCEEDED` · 源 1002 / 目标 1000 |
| `A3-runtime-quality.png` | 运行中心 · 质量报告 | `/runtime` | `PASSED` · 拒绝率 0.20% · 错误码分布 |
| `A4-safety-benchmark.png` | 安全治理与 Benchmark | `/safety` | Benchmark 门禁 + 证据时间线 **7 事件** |

数据口径：mock 引擎（`ETL_AGENT_ENGINE_MODE=mock`），Doris 目标表保持 0 行
（不搬数据）；质量指标为与 seed 形态一致的演示态合成值（1002/1000/2）；
证据 7 事件 = PDP + PREPARE + **APPROVAL_DECISION** + COMMIT + EXECUTION_TRANSITION×3。

**Pipeline Studio（`/studio`）不在清单内**：该页 version_id 取用存在契约缺口
（列表出参无 `pipeline_version_id`，前端误用 `pipeline_id` → 404 后回退演示态门禁），
另开 `fix/S10-studio-version-id` 独立修复，详见 `演示回放.md` §8.9 注。
