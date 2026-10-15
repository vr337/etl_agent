"""M-M 评测与安全进化 — Benchmark CLI 入口（`etl-agent-benchmark`）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 6.2 章 API-15
覆盖 FR：FR-50（版本化 Benchmark 套件的命令行执行）
对应 AC：AC-09（六项门禁实际值打印 + 退出码）、AC-14/15/16

**退出码**（S9 提示词 §8.7）：0 = PASS，1 = FAIL。
CI 门禁靠退出码判定，故 FAIL 必须返回 1（禁止「打印 FAIL 却退出 0」）。

用法：
    uv run etl-agent-benchmark --suite harness-v2 --version v1 --layers L0,L1
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.benchmark.service import BenchmarkResult, BenchmarkService
from etl_agent.config import settings
from etl_agent.infrastructure.database import engine
from etl_agent.infrastructure.models import Project


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="etl-agent-benchmark",
        description="运行 etl_agent 版本化 Benchmark 套件（SPEC FR-50 / AC-09）",
    )
    parser.add_argument("--suite", default="harness-v2", help="套件标识（默认 harness-v2）")
    parser.add_argument("--version", default="v1", help="冻结版本（默认 v1）")
    parser.add_argument(
        "--layers", default="L0,L1,L2", help="层级子集，逗号分隔（默认 L0,L1,L2）"
    )
    parser.add_argument("--root", default="benchmarks", help="套件根目录（默认 benchmarks）")
    parser.add_argument(
        "--json", action="store_true", help="以 JSON 输出（便于 CI 解析）"
    )
    return parser


def _parse_layers(raw: str) -> tuple[str, ...]:
    layers = tuple(item.strip().upper() for item in raw.split(",") if item.strip())
    if not layers:
        raise SystemExit("--layers 不能为空")
    return layers


def _print_report(result: BenchmarkResult) -> None:
    """打印六项门禁实际值 vs 阈值（S9 提示词 §8.7）。"""
    print(f"套件: {result.suite_id}/{result.manifest_version}")
    print(f"manifest_sha256: {result.manifest_sha256}")
    print(f"运行: {result.run_id}")
    print(f"状态: {result.status}")
    print(f"report_sha256: {result.report_sha256}")
    print("-" * 62)
    print(f"{'门禁':<32}{'实际值':>10}{'判定':>8}{'阈值':>12}")
    print("-" * 62)
    op_symbol = {"eq": "=", "ge": ">=", "le": "<="}
    for gate in result.gate_report.gates:
        mark = "PASS" if gate.passed else "FAIL"
        symbol = op_symbol.get(gate.operator, gate.operator)
        print(
            f"{gate.key:<32}{gate.actual:>10.4f}{mark:>8}"
            f"{symbol + format(gate.threshold, '.4f'):>12}"
        )
    print("-" * 62)
    if result.gate_report.failures:
        print("未达标门禁: " + ", ".join(result.gate_report.failures))
    else:
        print("六项门禁全部达标")


def _print_json(result: BenchmarkResult) -> None:
    import json

    print(
        json.dumps(
            {
                "run_id": str(result.run_id),
                "suite_id": result.suite_id,
                "manifest_version": result.manifest_version,
                "manifest_sha256": result.manifest_sha256,
                "status": result.status,
                "metrics": result.metrics,
                "gates": [item.to_dict() for item in result.gate_report.gates],
                "failures": list(result.gate_report.failures),
                "report_sha256": result.report_sha256,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


async def _ephemeral_project(session: AsyncSession) -> Project:
    """取一个归属项目行；库里没有项目时造一行临时项目。

    `benchmark_runs.project_id` 非空，故运行记录必须挂在某个项目下。
    该角色行只在本 CLI 的事务内可见，**随事务回滚一起消失**。
    """
    from sqlalchemy import select

    project = (await session.execute(select(Project).limit(1))).scalars().first()
    if project is None:
        project = Project(name="benchmark-cli")
        session.add(project)
        await session.flush()
    return project


async def _run(args: argparse.Namespace) -> int:
    """执行 Benchmark 并返回退出码（0 = PASS，1 = FAIL）。

    **无副作用**（S9 禁止事项：CI 门禁不得污染环境）：整个运行在**单事务内**完成，
    结束后**无条件回滚** —— 既不给开发库留下 benchmark_runs 记录，也不会残留
    为冒烟测试而造的临时项目行（见 `_ephemeral_project`）。
    """
    from etl_agent.harness.pdp.engine import PdpEngine
    from etl_agent.harness.policy.package import load_package

    layers = _parse_layers(args.layers)
    package = load_package(
        settings.policy_package_path,
        settings.policy_package_signature_path,
        settings.harness_public_key_list,
    )
    service = BenchmarkService(root=args.root, pdp_engine=PdpEngine(package))

    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            async with AsyncSession(bind=conn, join_transaction_mode="create_savepoint") as session:
                project = await _ephemeral_project(session)
                service._project_id = str(project.id)
                result = await service.run(
                    session,
                    suite_id=args.suite,
                    version=args.version,
                    layers=layers,
                )
                if args.json:
                    _print_json(result)
                else:
                    _print_report(result)
        finally:
            await transaction.rollback()
    return 0 if result.status == "PASS" else 1


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 主入口（返回退出码）。"""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
