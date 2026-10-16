# etl_agent 环境运维备忘（ops-notes）

> | 项 | 值 |
> |----|-----|
> | 版本 | v1.0 |
> | 日期 | 2026-10-16 |
> | 适用 | 本机开发 / e2e 演示（compose 全栈 + 宿主机 API） |
> | 来源 | T-SHADOW-01 PR-A / PR-B 真跑（run20~run26）实证踩坑 |
> | 原则 | **只记「会重复踩、且踩了看不出原因」的坑**；常规操作不在此列 |

---

## 一、清库前必须先停 API + worker + beat

### 现象
`.workbuddy-ai/tmp/reset-db.sql`（DO 循环 `TRUNCATE ... CASCADE` 清 public 全部业务表）
**执行时无报错、但库没清干净**——残留的 `execution_runs` / `agent_runs` 会让后端测试假阴性
（`test_repair_budget_exhausted_after_3_candidates` / `test_report_isolated_per_run` 报
`assert 1 == 2`），看起来像代码回归，实际是脏数据。

### 根因
`TRUNCATE` 要拿 **AccessExclusiveLock**，而运行中的 API（宿主机进程）、`etl_agent_worker`、
`etl_agent_beat` 正持有这些表的 **AccessShareLock**（beat 每 2s 扫一次 `harness_command_outbox`）。
Postgres 检测到死锁 → **中断 DO 块**，但 `psql` 继续往下走、退出码仍是 0。
如果用 `... > /dev/null 2>&1` 包一层，**错误直接被吞掉**，看起来像「执行成功」。

### 正确姿势
```bash
# 1) 停 API（宿主机进程，Ctrl+C 或 kill）
# 2) 停 worker 与 beat
docker stop etl_agent_worker etl_agent_beat
# 3) 清库（此时无人持锁）
docker exec -i etl_agent_postgres psql -U etl_agent -d etl_agent -v ON_ERROR_STOP=1 \
  < .workbuddy-ai/tmp/reset-db.sql
# 4) 校验（必须看到 0）
docker exec etl_agent_postgres psql -U etl_agent -d etl_agent -t -A -c \
  "SELECT 'projects='||(SELECT count(*) FROM projects)||' runs='||(SELECT count(*) FROM execution_runs);"
```

> **铁律**：清库命令**不要**加 `> /dev/null 2>&1`；加 `-v ON_ERROR_STOP=1`，且**事后必须查一次计数**。
> 见本项目 `.workbuddy-ai/memory/` 里「跑完 demo 必须先清 PG 再跑全量回归」的同类教训。

---

## 二、重建 worker 时**不要**连带 recreate beat

### 现象
执行
```bash
docker compose -f infra/docker-compose.yml --profile exec up -d --force-recreate worker beat
```
之后 `etl_agent_beat` 进入 `Restarting (2)` 死循环，日志刷
`HTTP status client error (403 Forbidden) for url (https://pypi.tuna.tsinghua.edu.cn/...whl)`。
后果：**beat 不起来 → 没有周期扫描 → outbox 行无人领取 → 作业卡在 `QUEUED`、
事件流为空**（run25 就是这么卡的，表现像「Worker 挂了」）。

### 根因
两个容器用的是**不同 Dockerfile**：
- `etl_agent_worker` → `infra/Dockerfile.worker`（依赖在**构建期**装好，启动不联网）
- `etl_agent_beat` → `infra/Dockerfile.api`（`Dockerfile.api:12` 也 `RUN uv sync --frozen --extra dev`
  烘焙了 `.venv`，`PATH` 已含 `/app/.venv/bin`）

但 compose 给 beat 的 command 是 `uv run celery -A etl_agent.worker beat ...` ——
**`uv run` 每次启动都会重新同步**（查 lock / 拉依赖），撞上镜像源临时 403 就起不来。
依赖本来就在镜像里，`uv run` 这层是多余的。

### 正确姿势
**只重建 worker**（beat 不动）：
```bash
docker compose -f infra/docker-compose.yml --profile exec build worker
docker compose -f infra/docker-compose.yml --profile exec up -d --force-recreate worker
```

**万一 beat 已被连带重建而卡住**，用镜像里烘焙好的 `.venv` 直接起（绕过 `uv run`，无需网络）：
```bash
docker rm -f etl_agent_beat
docker run -d --name etl_agent_beat --restart unless-stopped \
  --env-file infra/.env \
  -e ETL_AGENT_POSTGRES_HOST=postgres -e ETL_AGENT_POSTGRES_PORT=5432 \
  -e ETL_AGENT_REDIS_HOST=redis     -e ETL_AGENT_REDIS_PORT=6379 \
  -e ETL_AGENT_SEATUNNEL_URL=http://seatunnel:8080 \
  -e ETL_AGENT_ENGINE_MODE=real-cli \
  --network etl_agent_default \
  etl_agent-beat:latest celery -A etl_agent.worker beat --loglevel=info
```
校验：`docker logs etl_agent_beat` 应出现 `Scheduler: Sending due task outbox-scan (worker.scan_outbox)`。

> **长期修法**（待排期，属 infra 改动）：把 compose 里 beat 的 command 从
> `uv run celery ...` 改成 `celery ...`（`.venv` 已烘焙在 PATH 上）。本次未改，
> 以免与正在评审的 PR 冲突。

---

## 三、其他已实证的环境事实（顺手记，避免再摸索）

| 事实 | 说明 |
|---|---|
| **改哪层要重启哪层** | 生成侧代码（`llm/`）在 **API 进程**（宿主机 `scripts/run-api.py`）→ 改完必须**重启 API**；执行侧代码（`worker/`、`execution/`）在 **worker 容器** → 必须**重建 worker 镜像 + 重建 worker 容器**。两者都要，否则真跑用的是旧代码 |
| **真跑模式** | `ETL_AGENT_ENGINE_MODE=real-cli`（worker 走 SeaTunnel CLI 提交 + REST 查状态）；worker 启动日志会打 `engine_mode=...`，**真跑前必须核对这一行** |
| **worker 镜像自带引擎客户端** | `infra/Dockerfile.worker` 已含 JDK8 + SeaTunnel CLI + connector 白名单；`docker exec etl_agent_worker ls /opt/seatunnel/connectors/` 应为 **5 条目** |
| **Doris FE 卡 `wait catalog to be ready`** | BE 重启后心跳/注册失效时会出现，`docker restart etl_agent_doris_fe etl_agent_doris_be` 即恢复（本次环境：`apache/doris:fe-3.0.8` / `be-3.0.8`） |
| **工作区换行** | 本仓 `core.autocrlf` 生效，工作区文件**全 CRLF**；手工编辑时新行必须同为 CRLF，否则 `git diff` 会出现整文件重写 |
| **`tracking.md` 会被 IDE 重排** | 改完务必 `git diff --stat` 看行数是否是「内容级」，表格 padding 被 IDE 补空格会污染 diff |
