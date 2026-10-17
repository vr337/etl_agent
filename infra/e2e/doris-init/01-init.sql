-- ============================================================================
-- S10 T-22 端到端演示 — Doris 目标端初始化（demo-scenario-a）
--
-- SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 7.2 章（端到端演示链路 AC-10）/
--   第 3.2 章 M-K（原子 Swap）
-- 覆盖 FR：FR-39（原子 Swap）/ FR-43（质量报告）/ FR-46（回滚）
-- 对应 AC：**AC-10**（端到端演示链路）
--
-- 用途：在 Doris 上准备「宽表 + 影子表」两件套，支撑：
--   - 增量 upsert 写入 order_wide（唯一键模型，故用 UNIQUE KEY）；
--   - 原子 Swap：先写 order_wide_shadow，校验通过后 `ALTER TABLE ... REPLACE`。
--
-- 质量分流的落点（T-DOCS-01 修正，原注释与 PRD 矛盾）：
--   **被拒行写数据面错误表 `<目标表>_error`，保留原始值与违规原因**
--   （PRD 10.5 第 4 条「错误表例外条款」/ AC-02）；仅**报告 / 证据 / 快照**
--   只回聚合计数与错误码，不回传错误表明文（M-K 边界）。
--   错误表由运行期幂等建表（`execution/shadow.py::create_error_table_sql`），
--   不在此初始化——其业务列须全可空，不能 `LIKE` 主表（`amount NOT NULL`）。
--
-- 说明：Doris 建表为同步 DDL；`REPLACE`（原子替换）要求两表结构严格一致，
--   故 shadow 表用 `CREATE TABLE ... LIKE` 派生，避免结构漂移。
-- ============================================================================

CREATE DATABASE IF NOT EXISTS doris_dw;

USE doris_dw;

-- ---- 主宽表（增量 upsert 目标） ----
DROP TABLE IF EXISTS order_wide;

CREATE TABLE order_wide (
  order_id    BIGINT       NOT NULL COMMENT '订单主键',
  customer_id BIGINT       NOT NULL COMMENT '客户编号',
  amount      DECIMAL(18, 2) NOT NULL COMMENT '金额（元；已由分转换）',
  status      VARCHAR(16)  NOT NULL COMMENT '订单状态',
  address     VARCHAR(255) NULL COMMENT '归一化后地址',
  updated_at  DATETIME     NOT NULL COMMENT '增量水位列'
)
ENGINE = OLAP
UNIQUE KEY (order_id)
DISTRIBUTED BY HASH (order_id) BUCKETS 4
PROPERTIES (
  "replication_num" = "1",
  "enable_unique_key_merge_on_write" = "true"
);

-- ---- 影子表：结构与主表严格一致（`LIKE` 派生，禁止手写以免漂移） ----
DROP TABLE IF EXISTS order_wide_shadow;
CREATE TABLE order_wide_shadow LIKE order_wide;

-- ---- 质量分流的聚合事实落控制面 `quality_reports`，Doris 侧不建聚合表 ----
-- 历史：此处曾有 `etl_quality_summary`（run_id + error_code 聚合），但全仓 `src/`
--   零写入点、零消费者 → 演示库恒空表，且易被误读为「分流聚合走 Doris」。
--   T-SHADOW-04 已删表定义（聚合事实的唯一落点是控制面 `quality_reports`）。
--   保留下面的 DROP 只为清理历史环境残留的幽灵表（本脚本幂等可重复执行）。
DROP TABLE IF EXISTS etl_quality_summary;

-- ---- 演示账号：最小权限（只写 doris_dw） ----
-- 注意：Doris 的 GRANT 语法与 MySQL 不同（`GRANT ... ON db.* TO 'user'@'%'`）。
CREATE USER IF NOT EXISTS 'demo_writer'@'%' IDENTIFIED BY 'demo_writer_pwd_2026';
GRANT ALL ON doris_dw.* TO 'demo_writer'@'%';

SHOW TABLES FROM doris_dw;
