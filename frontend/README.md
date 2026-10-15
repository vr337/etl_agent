# ETL.Agent 前端控制台（M-P / T-19）

> SPEC 依据：`etl_agent-SPEC_v1.0.3-Baseline` 第 3.2 章 M-P（控制台）/ 第 7 章（页面原型）
> 覆盖 FR：FR-53 ~ FR-58
> 对应 AC：AC-10（端到端演示链路）、AC-17（版本 Diff 视图）等（各页详见组件文件头）

---

## 技术栈

| 项 | 版本 | 说明 |
| --- | --- | --- |
| Node | **24.16.0** | 与 S10 提示词口径一致（低版本可能触发 `EBADENGINE`） |
| npm | 11.13.0 | — |
| Vue | 3.5.x | `<script setup>` + Composition API |
| Vite | 5.4.x | 构建与开发服务器 |
| TypeScript | 5.6.x | `strict` + `noUnusedLocals` |
| Vue Router | 4.4.x | History 模式 |
| Pinia | 2.2.x | 状态管理（actor / notification） |
| axios | 1.7.x | HTTP 客户端 |

无 UI 组件库依赖——设计令牌与组件均在本仓库内实现（见 `src/assets/styles/main.css`），
目的是让「每个统计卡 / 表格 / 按钮」都可由原型图直接对照，而非被组件库默认样式遮蔽。

---

## 快速开始

```bash
cd frontend
npm install
npm run dev          # → http://127.0.0.1:5173
```

Vite 已配置代理：`/api` 与 `/health` → `http://127.0.0.1:8000`，无需处理跨域。

### 可用脚本

| 命令 | 作用 |
| --- | --- |
| `npm run dev` | 开发服务器（HMR） |
| `npm run build` | 类型检查 + 生产构建（`vue-tsc --noEmit && vite build`） |
| `npm run preview` | 预览构建产物 |
| `npm run typecheck` | 仅类型检查（`vue-tsc --noEmit`） |
| `npm run lint` | ESLint 检查 |

---

## 目录结构

```
frontend/
├─ index.html
├─ vite.config.ts              # @ 别名 + 开发代理
├─ tsconfig.json               # strict / noUnusedLocals / paths
├─ Dockerfile                  # 静态产物 + nginx（可选部署路径）
└─ src/
   ├─ main.ts                  # 应用入口
   ├─ App.vue                  # 布局分流（fullscreen / 控制台）
   ├─ api/                     # 后端契约层（类型 + 域模块）
   │  ├─ client.ts             # axios 实例 + X-Actor-ID 注入 + 错误统一提示
   │  ├─ types.ts              # 与后端 schemas 对齐的类型定义
   │  └─ *.ts                  # auth / connections / profiles / files / agentRuns /
   │                           # versions / harness / executions / rollback /
   │                           # evidence / benchmark / safety
   ├─ assets/styles/main.css   # 设计令牌 + 基础组件类（.card/.badge/.btn/.table/...）
   ├─ components/              # 通用组件（13 个）
   ├─ router/index.ts          # 路由表（含 fullscreen 元信息）
   ├─ stores/                  # Pinia（actor / notification）
   └─ views/                   # 页面（7 个）
```

---

## 页面与原型图对照

原型图位于 `docs/prototype/images/`。**5 大视图逐一对照实现**（S10 提示词第七节强制要求）。

| 路由 | 视图文件 | 原型图 | 页面要点 |
| --- | --- | --- | --- |
| `/dashboard` | `views/Dashboard.vue` | `S1-dashboard.png` | 4 统计卡、最近执行、待办告警、证据账本健康度、3 张分布图、生产边界提示 |
| `/datasources` | `views/DataSources.vue` | `S2-datasources.png` | 4 统计卡、连接登记表 + 表单、元数据探查与分级、文件资产、凭据托管与轮换 |
| `/studio` | `views/PipelineStudio.vue` | `S3-pipeline-studio.png` | 生成需求与澄清（中断/恢复）、候选产物（EtlPlan/HOCON）、14 项门禁、不可变版本 Diff、版本链 |
| `/runtime` | `views/RuntimeCenter.vue` | `S4-runtime-center.png` | 4 统计卡、执行监控表、运行监督快照、审批控制台、失败诊断与受管回滚 |
| `/safety` | `views/SafetyGovernance.vue` | `S5-safety-governance.png` | 4 统计卡、Benchmark 六项门禁、PDP 策略包、L0/L1/L2 分层、提案与受控发布、证据账本 |
| `/versions/diff` | `views/VersionDiff.vue` | —（AC-17 独立视图） | 版本对比（Schema / HOCON / 质量规则）+ artifact_sha256 事实 |
| `/bootstrap` | `views/Bootstrap.vue` | —（FR-58 初始化） | 项目名 + 管理员 subject + Bootstrap Token（经请求头）→ 写入本地身份 |

---

## 安全边界（前端侧红线）

这些边界在代码中有明确注释与实现，**不是文档承诺**：

| 边界 | 实现位置 | 行为 |
| --- | --- | --- |
| 生产禁直传身份头（AC-11） | `api/client.ts` `isActorHeaderAllowed()` | `import.meta.env.PROD` 时**恒为 false**，永不注入 `X-Actor-ID` |
| 响应零明文（AC-02） | 全站不渲染任何密码/密钥字段 | 凭据只提交、不回显；响应只含 Provider 与摘要 |
| 证据展示不泄露计算细节 | `components/EvidenceTimeline.vue` | 只展示 `sequence/event_type/actor/risk/policy_decision_id/event_hash`，不展示 payload 正文、不提供「重算链」入口 |
| 门禁不可放宽（AC-09） | `views/PipelineStudio.vue` | 门禁项只读、无「跳过」入口；未全过时前端也拦截冻结 |
| 只读监控 | `views/RuntimeCenter.vue` | 不触发原子 Swap、不做硬中断；Commit 仅签发 Capability |
| 状态机不在前端复制 | `api/safety.ts` | 前端只发起合法下一步，越级由后端 409 拒绝 |

---

## 演示态标注（诚实原则）

以下内容因后端无对应端点或依赖引擎，在界面上**显式标注「示意」**，不伪装为真实数据：

- **Dashboard 的 3 张分布图**（PDP 决策分布 / 副作用调用 / Capability 消耗）——人类裁决：静态值 + 「示意」徽标，不新增后端端点；
- **SafetyGovernance 的 PDP 策略包 9 条规则**与 `policy_package_sha256` / `signature`——同上；
- **PipelineStudio 的「SeaTunnel --check 通过」**——真实 check 依赖引擎，S10 为 mock engine 模式。

引擎口径（人类裁决 C）：仅「SeaTunnel REST 提交作业」被 mock，其余链路真实执行。

---

## 构建产物部署（可选）

```bash
npm run build            # → dist/
```

`Dockerfile` 提供 nginx 静态托管路径（把 `dist/` 拷入镜像）。
注意：生产部署时**不要**设置 `VITE_ALLOW_ACTOR_HEADER`——生产身份必须来自 trusted-gateway。
