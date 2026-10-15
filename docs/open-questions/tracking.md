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
| S7 | 1 | SeaTunnel REST `submit-job` NPE | 独立运维 | **T-ENGINE-01** |
| S7 | 6 | 调度环境继承口径 | 本阶段可收尾 | ✅ 人类裁决 A |

## 三、独立运维事项

### T-ENGINE-01：SeaTunnel REST submit-job NPE（跨 4 版本）

- **状态**：Open
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
- **影响**：
  - AC-16（L2 真实链路）未在真引擎上跑通
  - S10 演示走 mock 引擎 + 真实 Doris（人类裁决 C）
- **后续方案**（S10 后独立做，1~2 小时）：
  - ⭐ **CLI 提交**：改用 `seatunnel.sh --config <hocon>`（绕过 REST 路径）
  - 备选：深入 SeaTunnel 源码 / 提 GitHub issue
  - 可接受：保持 mock 引擎
- **关联**：SPEC S-Q4（SeaTunnel 部署形态）
- **当前镜像**：`apache/seatunnel:2.3.13`（官方）

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