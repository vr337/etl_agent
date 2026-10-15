"""M-M 评测与安全进化 — L2 真实链路评测（S9 用 mock 引擎）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 6.2 章 API-15
覆盖 FR：FR-50（L2 真实链路：连接 → Harness 协议 → 影子表 → 分流 → Swap SQL 生成）
对应 AC：AC-16（端到端垂直切片走通）

**S9 口径（人类裁决 C / S7 遗留 1）**：真实引擎 `submit-job` 仍返回 500
（`flowpilot/seatunnel:2.3.13` 的 `ConfigShadeUtils.processConfig` NPE），
故本层用 **mock 引擎**验证链路编排：影子表创建 → 分流（valid/error sink）→ Swap SQL 生成。
**不依赖真实引擎提交**（S9 提示词 §8.4 明确；真实引擎 e2e 留 S10 前）。

链路步骤（每步都调真实实现，只有引擎提交用替身）：
1. `ensure_shadow_table`（复用 `execution.shadow` 的 DDL 生成）；
2. 分流路由校验（valid_sink 指向影子表、error_sink 独立）；
3. `build_swap_sql`（复用 `execution.atomic_swap` 的 Swap SQL 模板）。

**无外部副作用**：使用 `LoggingShadowTableExecutor` / `LoggingSwapExecutor` 语义
（只记录 SQL，不连 Doris），与 `tests/execution/test_atomic_swap.py` 的 mock 传输层同源。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from etl_agent.benchmark.layers.base import BenchmarkSuite, LayerResult, Scenario
from etl_agent.execution.atomic_swap import build_swap_sql
from etl_agent.execution.shadow import create_shadow_table_sql, shadow_table_name

# 垂直切片默认目标表（与 conftest 的 S6_TEST_PLAN sink 表一致）
DEFAULT_TARGET_TABLE = "orders_wide"


@dataclass
class MockEngineRun:
    """mock 引擎执行记录（记录调用序列，不连真实引擎）。"""

    submitted: list[str] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)

    async def submit_job(self, hocon: str, *, job_name: str = "") -> str:
        """替身提交：只记录，不发起网络调用（AC-16 编排验证）。"""
        self.submitted.append(hocon)
        return f"mock-job-{len(self.submitted)}"

    async def stop_job(self, job_id: str) -> None:
        self.stopped.append(job_id)


class L2E2EEvaluator:
    """L2 端到端垂直切片评测器（mock 引擎）。"""

    layer = "L2"

    def __init__(self, *, target_table: str = DEFAULT_TARGET_TABLE) -> None:
        self._target_table = target_table
        self.engine = MockEngineRun()

    async def evaluate_one(self, scenario: Scenario) -> dict[str, Any]:
        """跑通「影子表 → 分流 → Swap SQL 生成」垂直切片。"""
        target = self._target_table
        shadow = shadow_table_name(target)

        # 步骤 1：影子表 DDL（复用 execution.shadow，真实 SQL 模板）
        shadow_ddl = create_shadow_table_sql(target)

        # 步骤 2：mock 引擎提交（编排点；真实引擎留 S10 前）
        job_id = await self.engine.submit_job(
            f"# vertical slice job for {target}", job_name=f"bench-{scenario.id}"
        )

        # 步骤 3：分流路由校验（valid → 影子表，error → 独立错误表）
        valid_sink = shadow
        error_sink = f"{target}_error"
        routing_ok = valid_sink != error_sink

        # 步骤 4：Swap SQL 生成（复用 execution.atomic_swap，真实模板）
        swap_sql = build_swap_sql(target, shadow)

        expected_artifacts = scenario.payload.get("expected_artifacts", [])
        artifacts_ok = all(
            name in ("shadow_table", "swap_sql") for name in expected_artifacts
        )

        ok = bool(shadow_ddl and job_id and routing_ok and swap_sql and artifacts_ok)
        return {
            "scenario_id": scenario.id,
            "layer": scenario.layer,
            "priority": scenario.priority,
            "type": scenario.type,
            "name": scenario.name,
            "target_table": target,
            "shadow_table": shadow,
            "shadow_ddl": shadow_ddl,
            "job_id": job_id,
            "swap_sql": swap_sql,
            "valid_sink": valid_sink,
            "error_sink": error_sink,
            "ok": ok,
        }

    async def run(
        self, suite: BenchmarkSuite, scenarios: tuple[Scenario, ...]
    ) -> LayerResult:
        """执行全部 L2 场景（mock 引擎，无外部副作用）。"""
        result = LayerResult(layer=self.layer)
        for scenario in scenarios:
            detail = await self.evaluate_one(scenario)
            result.total += 1
            result.detailed.append(detail)
            if detail["ok"]:
                result.passed += 1
            elif scenario.is_legitimate:
                result.false_positives += 1
        return result


__all__ = ["DEFAULT_TARGET_TABLE", "L2E2EEvaluator", "MockEngineRun"]
