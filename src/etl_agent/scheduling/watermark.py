"""M-K/T-14 调度 — 水印增量同步（推进 + watermark_window 记录，FR-41）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-K / 第 5.2 章 pipeline_schedules
（watermark_column / watermark_initial / watermark_lag_seconds）/ 第 6.1 章 API-11
覆盖 FR：FR-41（调度推进水印值并记录 `watermark_window`）、FR-23（增量契约）
对应 AC：AC-06（增量执行窗口可追溯）

窗口口径（左闭右开，避免相邻窗口重复搬运同一行）：

    window = [previous, now - lag_seconds)

- `previous` 取自上一次增量执行推进后的水印值；首次取 `watermark_initial`；
- `end = now - watermark_lag_seconds`：滞后窗用来容忍「写入库的事务尚未提交」的
  迟到数据（FR-23 水印增量契约），否则边界行会被永久漏掉；
- **推进即取窗口右端**：下一次的 `previous` 就是本次的 `end`，不额外存状态列
  （SPEC 5.2 未给 `pipeline_schedules` 水印状态列，禁止事项 7 不改契约）。

`watermark_window` 作为**治理事实**随调度触发的准备单落库（`impact_report` 内），
不进执行响应、不进日志正文（AC-02）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class WatermarkWindow:
    """一次增量同步的窗口事实（左闭右开）。"""

    column: str
    start: str
    end: str
    lag_seconds: int

    def as_payload(self) -> dict[str, object]:
        """转成可落 JSON 列的治理事实（不含任何业务数据正文）。"""
        return {
            "column": self.column,
            "start": self.start,
            "end": self.end,
            "lag_seconds": self.lag_seconds,
            "bounds": "[start,end)",
        }


def resolve_window(
    *,
    column: str,
    initial: str | None,
    lag_seconds: int,
    previous: str | None,
    now: datetime | None = None,
) -> WatermarkWindow:
    """计算本次增量窗口（FR-41）。

    Args:
        column: 水印列名（TIMESTAMP 列）。
        initial: 契约登记的初始水印值（首次执行用）。
        lag_seconds: 滞后秒数（>0 才有意义；非正即退化为「无滞后」）。
        previous: 上一次推进后的水印值；缺省回落到 `initial`。
        now: 当前时刻；缺省当前 UTC。

    Returns:
        WatermarkWindow: 窗口事实（`start` 为左端、`end` 为右端）。
    """
    moment = now or datetime.now(UTC)
    lag = max(int(lag_seconds), 0)
    start = previous or initial or ""
    end_moment = moment - timedelta(seconds=lag)
    return WatermarkWindow(
        column=column,
        start=start,
        end=end_moment.isoformat(),
        lag_seconds=lag,
    )


def advance(window: WatermarkWindow) -> str:
    """推进水印值：下一次执行的 `previous` 即本次窗口右端（FR-41）。"""
    return window.end


__all__ = ["WatermarkWindow", "advance", "resolve_window"]
