-- ============================================================================
-- S10 T-22 端到端演示 — Doris 目标端初始化（demo-scenario-a）
--
-- SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 7.2 章（端到端演示链路 AC-10）/
--   第 3.2 章 M-K（原子 Swap）
-- 覆盖 FR：FR-39（原子 Swap）/ FR-43（质量报告）/ FR-46（回滚）
-- 对应 AC：**AC-10**（端到端演示链路）
--
-- 用途：在 Doris 上准备「宽表 + 影子表 + 质量表」三件套，支撑：
--   - 增量 upsert 写入 order_wide（唯一键模型，故用 UNIQUE KEY）；
--   - 原子 Swap：先写 order_wide_shadow，校验通过后 `ALTER TABLE ... REPLACE`；
--   - 质量分流：被拒行只在 quarantine 表留**聚集计数 + 错误码**，
--     不落原始敏感值（与 M-K 边界一致）。
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

-- ---- 质量分流表：只存聚合事实，不回原始值（M-K 边界） ----
-- 注意：DUPLICATE KEY (run_id, error_code) 要求 KEY 列是 schema 的有序前缀，
--   故 error_code 必须排在第 2 位（Doris 报错 1105 Key columns should be a
--   ordered prefix of the schema），调整列序时勿把 error_code 移出前两列。
DROP TABLE IF EXISTS etl_quality_summary;

CREATE TABLE etl_quality_summary (
  run_id        VARCHAR(64)  NOT NULL COMMENT '执行标识',
  error_code    VARCHAR(64)  NOT NULL COMMENT '错误码（如 QUALITY_NOT_NULL_AMOUNT）',
  total_rows    BIGINT       NOT NULL COMMENT '总行数',
  passed_rows   BIGINT       NOT NULL COMMENT '通过行数',
  rejected_rows BIGINT       NOT NULL COMMENT '拒绝行数',
  reject_rate   DOUBLE       NOT NULL COMMENT '拒绝率 = rejected / total',
  error_count   BIGINT       NOT NULL COMMENT '该错误码命中行数',
  occurred_at   DATETIME     NOT NULL COMMENT '报告时间'
)
ENGINE = OLAP
DUPLICATE KEY (run_id, error_code)
DISTRIBUTED BY HASH (run_id) BUCKETS 2
PROPERTIES ("replication_num" = "1");

-- ---- 演示账号：最小权限（只写 doris_dw） ----
-- 注意：Doris 的 GRANT 语法与 MySQL 不同（`GRANT ... ON db.* TO 'user'@'%'`）。
CREATE USER IF NOT EXISTS 'demo_writer'@'%' IDENTIFIED BY 'demo_writer_pwd_2026';
GRANT ALL ON doris_dw.* TO 'demo_writer'@'%';

SHOW TABLES FROM doris_dw;
