"""9 类失败分类用例（T-16.13，FR-47）。

覆盖：9 类分类全可达、显式分类优先、异常类型映射、消息关键字兜底、
可重试性判定、UNKNOWN 兜底。
"""
from __future__ import annotations

from etl_agent.diagnosis.classifiers import (
    ALL_CATEGORIES,
    CATEGORY_CANCELLATION,
    CATEGORY_CONFIGURATION,
    CATEGORY_CONNECTIVITY,
    CATEGORY_CREDENTIALS,
    CATEGORY_ENGINE,
    CATEGORY_OBSERVABILITY,
    CATEGORY_SINK_WRITE,
    CATEGORY_SOURCE_SCHEMA,
    CATEGORY_UNKNOWN,
    classify,
)
from etl_agent.execution.errors import SwapQualityFailed, SwapSchemaMismatch
from etl_agent.worker.errors import SeatunnelUnavailable


def test_9_classifiers_defined() -> None:
    """9 类失败分类齐备且唯一。"""
    assert len(ALL_CATEGORIES) == 9
    assert len(set(ALL_CATEGORIES)) == 9
    assert set(ALL_CATEGORIES) == {
        "CONFIGURATION",
        "CREDENTIALS",
        "CONNECTIVITY",
        "SOURCE_SCHEMA",
        "SINK_WRITE",
        "ENGINE",
        "OBSERVABILITY",
        "CANCELLATION",
        "UNKNOWN",
    }


def test_all_categories_reachable_by_explicit() -> None:
    """9 类分类全可达（显式分类直通）。"""
    for category in ALL_CATEGORIES:
        result = classify(failure_category=category)
        assert result.category == category
        assert result.matched_by == "explicit"
        assert result.suggestion


def test_classify_configuration() -> None:
    """HOCON 语法错误 → CONFIGURATION（关键字兜底）。"""
    result = classify(message="hocon 语法错误：missing parameter at line 3")
    assert result.category == CATEGORY_CONFIGURATION
    assert result.retryable is False


def test_classify_credentials() -> None:
    """物化失败 / 鉴权失败 → CREDENTIALS。"""
    assert classify(message="credential materialization failed") .category == CATEGORY_CREDENTIALS
    assert classify(message="password 鉴权失败").category == CATEGORY_CREDENTIALS
    assert classify(message="unauthorized").category == CATEGORY_CREDENTIALS


def test_classify_connectivity() -> None:
    """网络不可达 / 超时 → CONNECTIVITY（可重试）。"""
    result = classify(message="connection refused: 连接超时")
    assert result.category == CATEGORY_CONNECTIVITY
    assert result.retryable is True


def test_classify_source_schema() -> None:
    """源 Schema 漂移 → SOURCE_SCHEMA。"""
    result = classify(message="column not found: 字段 id 类型不匹配")
    assert result.category == CATEGORY_SOURCE_SCHEMA
    assert result.retryable is False


def test_classify_sink_write() -> None:
    """目标写入失败 → SINK_WRITE。"""
    result = classify(message="permission denied: disk full")
    assert result.category == CATEGORY_SINK_WRITE


def test_classify_engine() -> None:
    """引擎内部错误 → ENGINE。"""
    result = classify(message="NullPointerException: engine internal error")
    assert result.category == CATEGORY_ENGINE
    assert result.retryable is True


def test_classify_observability() -> None:
    """OTel / 日志异常 → OBSERVABILITY。"""
    result = classify(message="otel exporter trace failed")
    assert result.category == CATEGORY_OBSERVABILITY


def test_classify_cancellation() -> None:
    """硬中断 → CANCELLATION（显式与关键字均可达）。"""
    explicit = classify(failure_category="CANCELLATION")
    assert explicit.category == CATEGORY_CANCELLATION
    assert explicit.retryable is False
    keyword = classify(message="监督硬中断：记录数超预算")
    assert keyword.category == CATEGORY_CANCELLATION


def test_classify_by_exception_type() -> None:
    """异常类型映射：SwapSchemaMismatch → SINK_WRITE；SeatunnelUnavailable → CONNECTIVITY。"""
    schema = classify(exception=SwapSchemaMismatch("schema mismatch"))
    assert schema.category == CATEGORY_SINK_WRITE
    assert schema.matched_by.startswith("exception:")
    engine = classify(exception=SwapQualityFailed("quality failed"))
    assert engine.category == CATEGORY_SINK_WRITE
    net = classify(exception=SeatunnelUnavailable("engine down"))
    assert net.category == CATEGORY_CONNECTIVITY
    assert net.retryable is True


def test_classify_unknown_fallback() -> None:
    """未分类 → UNKNOWN（默认按可重试处置）。"""
    result = classify(message="something totally unexpected")
    assert result.category == CATEGORY_UNKNOWN
    assert result.matched_by == "fallback"
    assert result.retryable is True


def test_explicit_takes_priority_over_keyword() -> None:
    """显式分类优先于关键字（防消息文案覆盖治理判决）。"""
    result = classify(failure_category="CANCELLATION", message="password timeout")
    assert result.category == CATEGORY_CANCELLATION
    assert result.matched_by == "explicit"


def test_retryable_judgment() -> None:
    """可重试性判定：配置 / 凭据 / 结构漂移不可重试；连接 / 引擎可重试。"""
    assert classify(failure_category=CATEGORY_CONFIGURATION).retryable is False
    assert classify(failure_category=CATEGORY_CREDENTIALS).retryable is False
    assert classify(failure_category=CATEGORY_SOURCE_SCHEMA).retryable is False
    assert classify(failure_category=CATEGORY_CONNECTIVITY).retryable is True
    assert classify(failure_category=CATEGORY_ENGINE).retryable is True
