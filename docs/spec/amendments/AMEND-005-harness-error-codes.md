# AMEND-005：Harness 与版本域错误码补充

- 状态：Accepted
- 日期：2026-10-15
- 关联：etl_agent-SPEC_v1.0.3-Baseline 第 6.4 章错误码体系 / 第 3.2 章 M-H/M-J / 第 6.1 章 API-08/09/10
- 来源：S6 阶段报告（DoD-20 / 提示词 §8.10 的「新增？」候选项）
- 处置：本追加文件**不改动 v1.0.3-Baseline 正文**；待产出 SPEC v1.0.4-Baseline 时合并到 6.4 章节

## 一、S6 新增（3 个）

| 段 | HTTP | 错误码 | 含义 | 定义位置 |
|----|------|--------|------|---------|
| HARNESS_ | 403 | `HARNESS_403_TOOL_NOT_REGISTERED` | 调用的命令 / 副作用不在注册清单内（FR-34：以注册清单判定，非 Agent 自报） | `src/etl_agent/harness/errors.py::ToolNotRegistered` |
| HARNESS_ | 401 | `HARNESS_401_CAPABILITY_INVALID` | Capability 签名验签失败、令牌哈希不匹配或 claims 绑定漂移（换制品 / 换工具 / 换环境 / 换主体均拒绝） | `src/etl_agent/harness/errors.py::CapabilityInvalid` |
| HARNESS_ | 409 | `HARNESS_409_LEASE_HELD` | 执行租约已被占用：同一准备单的同类型副作用并发重入（FR-37） | `src/etl_agent/harness/errors.py::LeaseHeld` |

新增理由：三者都是 SPEC 6.4 既有码表**无法表达**的独立失败类别。

- 「不在注册清单」与 `HARNESS_403_POLICY_DENY`（有 PDP 依据的策略拒绝）不同：它是**实现 / 配置错误**，
  需要不同处置（查注册表，不是提审批单）；
- 「令牌不可信」与 `HARNESS_401_CAPABILITY_EXPIRED`（合法但过期）不同：
  前者说明有人伪造或复用旧令牌，必须与过期分开计量；
- 「租约占用」此前被并入 `BIZ_409_STATE_CONFLICT`，但它描述的是**并发重入**而非状态机非法，
  混在一起会让 S7 Worker 的监控无法区分「权限问题」与「调度竞争」。

## 二、S6 复用（不新增，记录决策依据）

| 段 | HTTP | 错误码 | 用途 | 决策说明 |
|----|------|--------|------|---------|
| BIZ_ | 409 | `BIZ_409_STATE_CONFLICT` | 版本域状态机不允许、乐观锁 `lock_version` 冲突、版本未冻结、准备单状态机不允许、**Commit 指纹重验失败** | 提示词 §8.10 曾列出 `BIZ_409_VERSION_STATE_CONFLICT` / `BIZ_409_VERSION_OPTIMISTIC_LOCK` 两个候选新码；S6 人类裁决：**复用** `BIZ_409_STATE_CONFLICT`，因为三者对调用方的处置完全相同（重新读取版本 / 准备单后重试），单独设码只增加契约面。详见 S6 阶段报告「口径差异」1 |
| BIZ_ | 404 | `BIZ_404_NOT_FOUND` | Pipeline 版本不存在或跨项目、设计审批申请不存在、准备单不存在或跨项目 | SPEC 6.4 通用码，覆盖「含跨项目视同不存在」语义 |
| BIZ_ | 409 | `BIZ_409_IDEMPOTENCY_CONFLICT` | 同幂等键不同载荷的准备单 | SPEC 6.4 已定义 |
| BIZ_ | 410 | `BIZ_410_PREPARATION_EXPIRED` | 准备单 TTL 过期（默认 24h） | SPEC 6.4 已定义 |
| HARNESS_ | 403 | `HARNESS_403_POLICY_DENY` | PDP DENY（跨项目 / Secret 外发） | SPEC 6.4 已定义 |
| HARNESS_ | 409 | `HARNESS_409_SELF_APPROVAL` | 申请人自批 / 同一自然人占多个审批槽 | SPEC 6.4 已定义 |
| HARNESS_ | 409 | `HARNESS_409_ROLE_TAKEN` | 职责槽被占 | SPEC 6.4 已定义 |
| HARNESS_ | 409 | `HARNESS_409_ALREADY_COMMITTED` | 重复 Commit | SPEC 6.4 已定义 |
| HARNESS_ | 409 | `HARNESS_409_APPROVAL_INSUFFICIENT` | 审批票数 / 槽位不足 | SPEC 6.4 已定义 |
| HARNESS_ | 409 | `HARNESS_409_CAPABILITY_REPLAYED` | 令牌二次消费 / 令牌与登记摘要不一致 | SPEC 6.4 已定义 |
| HARNESS_ | 401 | `HARNESS_401_CAPABILITY_EXPIRED` | 令牌过期 | SPEC 6.4 已定义 |
| AUTH_ | 403 | `AUTH_403_ROLE_REQUIRED` | 无对应 Harness 职责（含 API-10 非 OPERATOR） | SPEC 6.4 已定义 |
| AUTH_ | 403 | `AUTH_403_NOT_PROJECT_MEMBER` | 非项目成员访问 Harness / 版本资源 | SPEC 6.4 已定义 |

## 三、启动期失败（非 API 错误码，不入错误码体系）

两种「可信密钥 / 策略包不可用」的失败选择**拒绝启动**而非对外返回错误码：

- `ProductionConfigError`（`src/etl_agent/config.py`）：Harness 配置缺失 / TTL 越界 / 生产缺密钥；
- `PolicyPackageInvalid`（`src/etl_agent/harness/policy/package.py`）：策略包缺失、清单被篡改、签名验签失败；
- `CapabilityIssuanceError`（继承 `PolicyPackageInvalid`）：签名私钥缺失或非 32 字节 hex。

理由：降级放行等于在没有可信策略的情况下接收执行意图（SPEC 8「安全 · 授权与职责分离」行），
这比拒绝启动危险得多。

## 四、待人类裁决

1. 上述 3 个新码的 HTTP 状态与命名是否符合预期（尤其 `HARNESS_401_CAPABILITY_INVALID` 的 401 段位选择）；
2. `HARNESS_409_LEASE_HELD` 是否值得独立设码（还是并入 `BIZ_409_STATE_CONFLICT`）；
3. 版本域统一复用 `BIZ_409_STATE_CONFLICT` 是否接受（提示词 §8.10 的 2 个候选新码因此不再产生）。

## 处理

- 在此之前，S6+ 阶段的错误码清单 = SPEC 6.4 + AMEND-001（Bootstrap）+ AMEND-002（连接域）+ AMEND-003（探查域 / 文件资产域）+ AMEND-004（生成域）+ **本追加文件（Harness / 版本域）**；
- 审核动作：人类确认后 `git add docs/spec/amendments/AMEND-005-harness-error-codes.md` + `git commit`，状态改为 Accepted；
- **本草稿不随 S6 提交自动入库**（S6 禁止事项 22：AMEND 草稿由人类审核后提交）。

## 口径裁决（S6 收尾确认）

1. `HARNESS_401_CAPABILITY_INVALID` 保持 401 段位（与 CAPABILITY_EXPIRED 同段，便于前端统一处理）；
2. `HARNESS_409_LEASE_HELD` 独立设码（并发重入 vs 状态机非法，语义不同，便于 S7 监控区分）；
3. 版本域复用 `BIZ_409_STATE_CONFLICT` 已接受（S6 启动前裁决，不新增两个专用码）。
