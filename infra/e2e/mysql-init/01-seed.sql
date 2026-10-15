-- ============================================================================
-- S10 T-22 端到端演示 — MySQL 源端种子数据（demo-scenario-a）
--
-- SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 7.2 章（端到端演示链路 AC-10）/
--   第 3.2 章 M-E（profiles）
-- 覆盖 FR：FR-07（元数据探查）/ FR-38（执行监控）/ FR-43（质量报告）
-- 对应 AC：**AC-10**（端到端演示链路）
--
-- 用途：compose `--profile e2e` 起 MySQL 后，本脚本由容器的
--   `/docker-entrypoint-initdb.d/` 自动执行，准备一张可被「增量同步」的订单表。
--
-- 设计要点（为什么这样造数）：
--  1. `orders` 表刻意包含 **脏数据**（amount 为 NULL、updated_at 越界），
--     以便演示质量契约的「错误分流」（reject_route），拒绝率非零但可控；
--  2. `updated_at` 作为增量水位列（对应生成期澄清问题「orders.updated_at 是否
--     可作为增量水位列」），故必须有近实时时间戳，否则增量拉不到数据；
--  3. 主键 order_id 为奇偶混合，便于演示 upsert（重复键更新而非插入）。
-- ============================================================================

CREATE DATABASE IF NOT EXISTS source_db
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE source_db;

DROP TABLE IF EXISTS orders;

CREATE TABLE orders (
  order_id     BIGINT       NOT NULL COMMENT '订单主键',
  customer_id  BIGINT       NOT NULL COMMENT '客户编号',
  amount       DECIMAL(18, 2) DEFAULT NULL COMMENT '金额（分；脏数据含 NULL）',
  status       VARCHAR(16)  NOT NULL DEFAULT 'PAID' COMMENT '订单状态',
  address      VARCHAR(255) DEFAULT NULL COMMENT '收货地址（待归一化）',
  updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '增量水位列',
  PRIMARY KEY (order_id),
  KEY idx_orders_updated_at (updated_at),
  KEY idx_orders_customer (customer_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '订单源表（演示用）';

-- ---- 干净数据：1,000 行，金额为「分」整数（生成期规则 amount / 100.0 转元） ----
INSERT INTO orders (order_id, customer_id, amount, status, address, updated_at)
SELECT
  seq.n,
  1000 + (seq.n % 100),
  1200 + (seq.n * 137 % 90000),
  CASE seq.n % 5
    WHEN 0 THEN 'PAID'
    WHEN 1 THEN 'SHIPPED'
    WHEN 2 THEN 'PAID'
    WHEN 3 THEN 'REFUNDED'
    ELSE 'PAID'
  END,
  CONCAT('浙江省杭州市西湖区文三路', 100 + (seq.n % 900), '号'),
  NOW() - INTERVAL (seq.n % 600) SECOND
FROM (
  SELECT
    ones.n + tens.n * 10 + hundreds.n * 100 + 1 AS n
  FROM
    (SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
     UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) ones
  CROSS JOIN
    (SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
     UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) tens
  CROSS JOIN
    (SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
     UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) hundreds
) seq
WHERE seq.n <= 1000;

-- ---- 脏数据：3 行（演示「错误分流」，非阻断整批） ----
-- 1) amount 为 NULL → 触发 not_null(amount) 质量规则
INSERT INTO orders (order_id, customer_id, amount, status, address, updated_at)
VALUES (9001, 7777, NULL, 'PAID', '地址缺失', NOW() - INTERVAL 30 SECOND);

-- 2) updated_at 越界（未来时间）→ 触发水位列合理性
INSERT INTO orders (order_id, customer_id, amount, status, address, updated_at)
VALUES (9002, 7778, 888800, 'PAID', '北京市朝阳区建国路 1 号', NOW() + INTERVAL 365 DAY);

-- 3) 地址超长 → 触发归一化后的长度约束
INSERT INTO orders (order_id, customer_id, amount, status, address, updated_at)
VALUES (9003, 7779, 1200, 'PAID', REPEAT('X', 400), NOW() - INTERVAL 10 SECOND);

-- ---- 演示账号：最小权限（只读 source_db；凭据由平台 SecretProvider 托管） ----
CREATE USER IF NOT EXISTS 'demo_reader'@'%' IDENTIFIED BY 'demo_reader_pwd_2026';
GRANT SELECT, RELOAD, SHOW DATABASES, REPLICATION SLAVE, REPLICATION CLIENT
  ON *.* TO 'demo_reader'@'%';
FLUSH PRIVILEGES;

SELECT
  (SELECT COUNT(*) FROM orders) AS total_rows,
  (SELECT COUNT(*) FROM orders WHERE amount IS NULL) AS null_amount_rows,
  (SELECT MAX(updated_at) FROM orders) AS watermark_max;
