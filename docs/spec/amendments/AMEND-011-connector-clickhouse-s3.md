# AMEND-011：连接器枚举扩展（ClickHouse / S3 对象存储 / REST API）

- 状态：**Draft（待人类审核）**
- 日期：2026-10-17（原型图补于 2026-10-18）
- 关联：etl_agent-SPEC_v1.0.3-Baseline 第 5.2 章 `data_connections.connector_type` /
  第 3.2 章 M-C（数据连接）/ M-E（文件资产）
- 来源：S11 T-21 条件数据源适配（`feat/S11-clickhouse`、`feat/S11-s3-minio`）
- 处置：本追加文件**不改动 v1.0.3-Baseline 正文**；待产出 SPEC v1.0.4-Baseline 时合并到 5.2 章节

## 变更内容

### 1. `connector_type` 枚举由 8 类扩为 11 类

新增三类，均**不改动**既有 8 类的语义与取值：

| 类型 | 接入通道 | 连通性探针 | 元数据探查 | 生成引擎（HOCON） |
| --- | --- | --- | --- | --- |
| `CLICKHOUSE` | HTTP 接口 8123 | `SELECT 1` | `system.columns` / `system.parts` | ✅ JDBC（`com.clickhouse.jdbc.ClickHouseDriver`） |
| `S3` | S3 API（SigV4，path-style） | `ListObjectsV2`（max-keys=1） | 不适用（对象前缀列举） | ❌ 未接入，另行排期 |
| `REST_API` | HTTP/HTTPS（GET） | 一次 GET，只判状态码 | 响应样本 → `infer_columns` 冻结 Schema | ❌ 未接入，另行排期 |

**选型说明**：

- ClickHouse 走 **HTTP 8123** 而非 native 9000 或 MySQL 兼容 9004：
  native 需新增 `clickhouse-driver` / `clickhouse-connect` 依赖；MySQL 协议默认关闭且不保证开启。
  HTTP 接口用既有 `httpx` 即可，**零新增依赖**。
- S3 的 SigV4 签名为**手写实现**（`hmac` + `hashlib`，约 60 行）而非引入 boto3：
  仅需 ListObjectsV2 一个只读操作，boto3 会连带 `botocore` / `s3transfer` / `jmespath`
  且为同步 SDK，与全异步栈需额外包装。
- REST API 不引 `requests` / 不引任何 SDK：`httpx` 已是既有依赖；分页契约
  （`NONE` / `PAGE` / `OFFSET` / `CURSOR`）写成**显式声明**而非自动探测，
  避免把服务端约定猜错。

### 2. 连接参数映射（复用既有列，**不新增迁移**）

| 列 | ClickHouse 语义 | S3 语义 | REST API 语义 |
| --- | --- | --- | --- |
| `host` | 主机名 | 端点，可带 scheme（`https://s3.amazonaws.com` / `http://127.0.0.1`） | Base URL（`https://api.example.com/v1`） |
| `port` | HTTP 端口（默认 8123） | 端点端口（`https` 默认 443，MinIO 常用 9000） | 端口（URL 自带时以 URL 为准） |
| `database_name` | 库名 | Bucket 名 | 资源路径（如 `/orders`），可空 |
| `username` / `password` | 账号 / 口令 | Access Key / Secret Key（经 SecretProvider 托管） | 认证：`username` 空 → Bearer(`password`)；非空 → Basic |

### 3. UI 原型（S11 新增，Pencil 绘制）

两张表单均沿用「数据源与资产」页面既有风格（浅灰底 + 白卡 + 蓝色主行动作）：

| 原型图 | 路径 | 覆盖字段 |
| --- | --- | --- |
| ClickHouse 连接表单 | `docs/prototype/S11-clickhouse-form.png` | Host / Port / Database / Username / Password（密码框）· 「测试连通性」 |
| S3 / MinIO 连接表单 | `docs/prototype/S11-minio-form.png` | Endpoint / Access Key / Secret Key（密码框）· Bucket Name · 「测试连通性」 |

> 字段与后端入参一一对应（见上表「连接参数映射」）；两张表单底部的脚注
> 「凭据经 SecretProvider 加密托管，响应与日志零明文（AC-02）」对应既有安全约束，
> 不引入新要求。
>
> **设计源文件**：`docs/prototype/S11-conditional-sources.pen`（Pencil 2.21 格式，
> 含 `S3 Form Board` / `ClickHouse Form Board` 两个画板）。PNG 由该源文件导出，
> 两者同源，改稿请改 `.pen` 后重新导出，避免图与源漂移。
>
> `REST_API` 连接表单本轮**未出图**（人类仅指定上述两张）；其字段映射见
> 第 2 节表格（Base URL / 资源路径 / 认证），实现落地后再补原型。

### 4. 门禁白名单的**非对称**取舍（需注意）

| 门禁 | 是否纳入 | 理由 |
| --- | --- | --- |
| GATE-002（可生成连接器） | 仅 `CLICKHOUSE` | `S3` / `REST_API` 尚未接入 HOCON 渲染，纳入会让「渲染不出来的 plan」通过门禁 |
| GATE-011（数据库端点） | 仅 `CLICKHOUSE` | `S3` / `REST_API` 不是数据库端点，其资源归属由连接 `project_id` 保证（AC-12） |

## 影响面

- `src/etl_agent/connections/schemas.py` — `CONNECTOR_TYPES`
- `src/etl_agent/connections/clickhouse.py`、`connections/s3.py`、
  `connections/rest_api.py`（新增）
- `src/etl_agent/connections/probes/{clickhouse_probe,s3_probe,rest_api_probe}.py`（新增）
- `src/etl_agent/connections/router.py` — 新增 `GET .../connections/{id}/objects`
- `src/etl_agent/profiles/{introspect,statistics}.py` — ClickHouse 通道
- `src/etl_agent/gate/rules/{supported_connectors,database_endpoints}.py`
- `src/etl_agent/llm/mock_provider.py` — ClickHouse JDBC 映射
- `frontend/src/api/types.ts`、`views/DataSources.vue`

## 不涉及

- 不改动 Baseline 正文、错误码表（6.4）、FR / AC 编号与语义
- 不新增数据库列 / 迁移（连接参数复用既有列）
- 不改动既有 8 类连接器的探针与探查行为

## 审核动作

- 人类确认后，本文件状态改为 **Accepted**；
- 未确认前，代码侧按 Draft 实现，回滚点为 revert 三个 PR
  （`feat/S11-clickhouse` / `feat/S11-s3-minio` / `feat/S11-rest-api`）——
  三者按该顺序 stacked，回滚需**从后往前**。
