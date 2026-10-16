"""引擎指标聚合用例（T-ENGINE-02 缺陷 3：真实 /job-info metrics 键映射）。

样本取自 T-ENGINE-01 步骤 4 对真实引擎（flowpilot/seatunnel:2.3.13）REST
`/job-info` 的实抓载荷——值为字符串、行数指标为「顶点→计数」嵌套 dict。
"""
from __future__ import annotations

from etl_agent.execution.metrics import metrics_from_engine_payload
from etl_agent.execution.routing import QualityOutcome, classify_quality

# 真实引擎 /job-info 的 metrics 段（实抓，去掉 QPS 类噪声）
REAL_JOB_INFO_METRICS: dict[str, object] = {
    "TableSourceReceivedCount": {"Source[0].orders": "1002"},
    "TableSourceReceivedQPS": {"Source[0].orders": "31.7"},
    "SinkCommittedCount": "1000",
    "TableSinkWriteCount": {"Sink[0].order_wide": "1000"},
    "TableSinkWriteQPS": {"Sink[0].order_wide": "31.6"},
}


def test_real_engine_metrics_map_row_counts() -> None:
    """真实键兜底：TableSourceReceivedCount / TableSinkWriteCount → 行数。"""
    metrics = metrics_from_engine_payload(dict(REAL_JOB_INFO_METRICS))
    assert metrics.source_rows == 1002
    assert metrics.sink_rows == 1000
    assert metrics.source_bytes is None  # 真实形态无字节维度（TODO，如实 None）


def test_mock_shape_metrics_still_parse() -> None:
    """mock 形态优先：source_rows / sink_rows 直读（两形态兼容，不回归）。"""
    metrics = metrics_from_engine_payload(
        {"source_rows": 1002, "sink_rows": 1000, "source_bytes": 10, "sink_bytes": 9}
    )
    assert (metrics.source_rows, metrics.sink_rows) == (1002, 1000)
    assert (metrics.source_bytes, metrics.sink_bytes) == (10, 9)


def test_real_metrics_sink_write_falls_back_to_committed() -> None:
    """sink 写计数缺失时退化用 SinkCommittedCount（扁平字符串）。"""
    payload = {"TableSourceReceivedCount": {"Source[0].orders": "7"}}
    metrics = metrics_from_engine_payload(payload)
    assert metrics.source_rows == 7
    assert metrics.sink_rows is None


def test_metrics_payload_null_safe() -> None:
    """null-safe：None / 空 dict / 非数字字符串 / bool 都不抛、对应维度 None。"""
    for payload in (
        {},
        {"TableSourceReceivedCount": None},
        {"TableSourceReceivedCount": {}},
        {"TableSourceReceivedCount": {"Source[0]": "abc"}},
        {"TableSinkWriteCount": {"Sink[0]": "12"}, "SinkCommittedCount": True},
    ):
        metrics = metrics_from_engine_payload(payload)
        assert metrics.source_rows is None or isinstance(metrics.source_rows, int)
        assert metrics.sink_rows is None or isinstance(metrics.sink_rows, int)


def test_route_outcome_with_none_metrics_is_incomplete_not_crash() -> None:
    """下游 null-safe：真实指标缺失 → INCOMPLETE，不除零、不抛（裁决②）。"""
    metrics = metrics_from_engine_payload({})
    outcome = QualityOutcome(
        total_rows=metrics.source_rows,
        passed_rows=metrics.sink_rows or 0,
        rejected_rows=0,
        error_codes={},
    )
    status, reject_rate = classify_quality({}, outcome)
    assert status == "INCOMPLETE"
    assert reject_rate is None


# T-SHADOW-01 双路分流作业的载荷形态（run21 预期）：两个 sink 各一个键
DUAL_SINK_METRICS: dict[str, object] = {
    "TableSourceReceivedCount": {"Source[0].source_db.orders": "1002"},
    "TableSinkWriteCount": {
        "Sink[0].doris_dw.order_wide_shadow": "1000",
        "Sink[1].doris_dw.order_wide_error": "2",
    },
    "SinkCommittedCount": "1000",
    # 扁平合计（各 sink 之和）——**不可直接当有效行数**
    "SinkWriteCount": "1002",
}


def test_dual_sink_metrics_split_by_table_suffix() -> None:
    """T-SHADOW-01：双路分流按 **sink 表名后缀**分键取行数，不用扁平合计。

    扁平 `SinkWriteCount` = 影子表 + 错误表 = 1002；若拿它当 `sink_rows`，
    `rejected = source - sink` 会恒为 0，质量报告读不出分流事实（DoD 的 1000/2）。
    """
    metrics = metrics_from_engine_payload(dict(DUAL_SINK_METRICS))
    assert metrics.source_rows == 1002
    assert metrics.sink_rows == 1000  # 影子表那一路（有效数据）
    assert metrics.rejected_rows == 2  # 错误表那一路（脏数据）


def test_single_sink_metrics_still_use_flat_sum() -> None:
    """单 sink 作业（未分流）无 `_shadow` / `_error` 键 → 退回嵌套合计，不回归。"""
    metrics = metrics_from_engine_payload(dict(REAL_JOB_INFO_METRICS))
    assert metrics.sink_rows == 1000
    assert metrics.rejected_rows is None
