# etl_agent

生产级 ETL-Agent 数据集成平台：以 LangGraph 状态机完成「自然语言需求 → 受管元数据探查 → 结构化 EtlPlan 与 SeaTunnel HOCON 生成 → 确定性门禁 → 不可变制品 → 四眼审批 → 单次能力授权 → 受管执行 → 质量契约发布」全闭环；控制面治理与数据面搬运（Apache SeaTunnel）严格分离。

## 基线

- PRD：`docs/prd/etl_agent-PRD_v1.1.1-Baseline.md`（BL-PRD-001）
- SPEC：`docs/spec/etl_agent-SPEC_v1.0.3-Baseline.md`（BL-SPEC-002）

## 技术栈

Python 3.12 · FastAPI · SQLAlchemy（异步）+ Alembic · Celery + Redis · LangGraph + Postgres Checkpointer · PostgreSQL 17 · Redis 8 · 包管理 [uv](https://docs.astral.sh/uv/)（ADR-08）。

## 快速开始（Windows + Docker Desktop）

```powershell
# 1. 基础设施（PostgreSQL 17 + Redis 8，数据卷 bind mount 到 D 盘）
docker compose -f infra/docker-compose.yml up -d

# 2. 固定 Python 并同步依赖（含 dev extra）
uv python pin 3.12
uv sync --extra dev

# 3. 业务库迁移（可升可降）
uv run alembic upgrade head
uv run alembic downgrade base
uv run alembic upgrade head

# 4. Checkpoint 初始化（幂等，可重复执行）
uv run python -m etl_agent.checkpoint_setup

# 5. 单元测试
uv run pytest tests/infrastructure/ -v

# 6. 启动 API（:8000）
uv run etl-agent-api
```

## 目录结构

```
etl_agent/
├── alembic/                 # 业务库迁移（versions/0001_initial_schema.py 首版）
├── docs/                    # PRD / SPEC 基线（冻结）、原型图、阶段提示词
├── infra/                   # docker-compose.yml、postgres-init.sql、.env（本地，不入库）
├── src/etl_agent/
│   ├── main.py              # FastAPI 入口（etl-agent-api）
│   ├── checkpoint_setup.py  # etl_checkpoint 库初始化（etl-agent-checkpoint-setup）
│   └── infrastructure/      # base / database / models（41 张业务表）
└── tests/infrastructure/    # 模型与关键约束测试
```

## 双库分离（ADR-03）

| 库 | 内容 | 管理方式 |
|----|------|---------|
| `etl_agent` | 业务表（SPEC 5.2 建表清单） | Alembic（`uv run alembic upgrade head`） |
| `etl_checkpoint` | LangGraph Checkpointer 表（对话/工作流状态） | `uv run python -m etl_agent.checkpoint_setup` |

初始化顺序：先 `alembic upgrade head`，再 `checkpoint_setup`（PRD 9.4 第 2 条）。

## 环境变量

统一 `ETL_AGENT_` 前缀；模板见 `.env.example`（凭据一律 CHANGE_ME 占位，真实值只进本地 `.env` / `infra/.env`，不入库）。开发期连接件从 `infra/.env` 读取。

## 数据模型约束（SPEC 9.10）

枚举一律 String 持久化（禁止 PG native enum，ADR-07）· 项目级资源带 `project_id` 与隔离索引 · 大整数 BigInteger · 时间戳 `timestamptz` · `pipeline_versions.lock_version` 乐观锁 · 业务库禁止存 Prompt / 候选正文 / 凭证明文。

## 常用命令

| 操作 | 命令 |
|------|------|
| 同步依赖 | `uv sync --extra dev` |
| 加依赖 | `uv add <pkg>` |
| 迁移 | `uv run alembic upgrade head` |
| 测试 | `uv run pytest tests/infrastructure/ -v` |
| Lint / 类型 | `uv run ruff check src/ tests/` ；`uv run mypy src/etl_agent/infrastructure/` |
