# S10 场景 A 演示截图（与 run16 对齐）

用途：`docs/demo/演示回放.md` 第 8 节「场景 A 主链路演示」的前端对照截图。
生成时点：12 步跑到 STEP 11 后（执行 / 质量报告 / 证据均已落库）。

| 文件 | 视图 | 观察点 |
| --- | --- | --- |
| `A0-dashboard.png` | Dashboard 总览 | 真实计数（1 项目 / 2 连接 / 2 Profile / 1 执行 / 1 质量） |
| `A1-runtime-execution.png` | RuntimeCenter 执行详情 | `SUCCEEDED` · 源 1002 / 目标 1000 |
| `A2-runtime-quality.png` | RuntimeCenter 质量报告 | `PASSED` · 拒绝率 0.20% · 错误码分布 |
| `A3-pipeline-studio-version.png` | PipelineStudio 版本视图 | `VALIDATED` · 门禁 14/14 |
| `A4-safety-evidence-timeline.png` | SafetyGovernance 证据时间线 | 6 事件（含哈希链） |

数据口径：mock 引擎（`ETL_AGENT_ENGINE_MODE=mock`），Doris 目标表保持 0 行
（不搬数据）；质量指标为与 seed 形态一致的演示态合成值（1002/1000/2）。
