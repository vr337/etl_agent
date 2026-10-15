"""S9 Benchmark — CLI 入口用例（退出码 + 报告打印）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-M / 第 6.2 章 API-15
覆盖 FR：FR-50（版本化 Benchmark 套件的命令行执行）
对应 AC：AC-09（六项门禁实际值打印 + 退出码）、AC-14/15/16

**退出码红线**（S9 提示词 §8.7）：0 = PASS，1 = FAIL。
CI 门禁靠退出码判定，故 FAIL 绝不能退出 0。
"""

from __future__ import annotations

import json
import uuid

import pytest

from etl_agent.benchmark import cli as benchmark_cli
from etl_agent.benchmark.gates import GateOutcome, GateReport
from etl_agent.benchmark.service import BenchmarkResult


def _parser_defaults() -> dict:
    parser = benchmark_cli._build_parser()
    return vars(parser.parse_args([]))


def test_parser_defaults() -> None:
    """CLI 默认参数：harness-v2 / v1 / 三层 / benchmarks 根。"""
    args = _parser_defaults()
    assert args["suite"] == "harness-v2"
    assert args["version"] == "v1"
    assert args["layers"] == "L0,L1,L2"
    assert args["root"] == "benchmarks"
    assert args["json"] is False


def test_parse_layers_normalizes_and_validates() -> None:
    """层解析：大小写归一 + 去空；空串即报错退出。"""
    assert benchmark_cli._parse_layers("l0, l1 , l2") == ("L0", "L1", "L2")
    assert benchmark_cli._parse_layers("L0") == ("L0",)
    with pytest.raises(SystemExit):
        benchmark_cli._parse_layers(" , ")


def _result(status: str, *, json_mode: bool = False) -> BenchmarkResult:
    gates = tuple(
        GateOutcome(key=key, actual=1.0, operator="eq", threshold=1.0, passed=status == "PASS")
        for key in (
            "p0_p1_block_rate",
            "legitimate_success_rate",
            "false_positive_rate",
            "evidence_coverage_rate",
            "reproducibility_rate",
            "non_llm_p95_regression",
        )
    )
    return BenchmarkResult(
        run_id=uuid.UUID(int=1),
        suite_id="harness-v2",
        manifest_version="v1",
        manifest_sha256="a" * 64,
        status=status,
        metrics={"p0_p1_block_rate": 1.0},
        gate_report=GateReport(
            passed=status == "PASS",
            metrics={"p0_p1_block_rate": 1.0},
            gates=gates,
            failures=() if status == "PASS" else ("p0_p1_block_rate",),
        ),
        report_sha256="b" * 64,
    )


def test_print_report_pass(capsys: pytest.CaptureFixture[str]) -> None:
    """文本报告：打印六项门禁与 status。"""
    benchmark_cli._print_report(_result("PASS"))
    out = capsys.readouterr().out
    assert "harness-v2/v1" in out
    assert "PASS" in out
    assert "六项门禁全部达标" in out
    for key in (
        "p0_p1_block_rate",
        "legitimate_success_rate",
        "false_positive_rate",
        "evidence_coverage_rate",
        "reproducibility_rate",
        "non_llm_p95_regression",
    ):
        assert key in out


def test_print_report_fail_lists_failures(capsys: pytest.CaptureFixture[str]) -> None:
    """文本报告：FAIL 时列出未达标门禁（可定位失守项）。"""
    benchmark_cli._print_report(_result("FAIL"))
    out = capsys.readouterr().out
    assert "未达标门禁" in out
    assert "p0_p1_block_rate" in out


def test_print_json_parseable(capsys: pytest.CaptureFixture[str]) -> None:
    """JSON 输出可被 CI 解析（含 gates 与 failures）。"""
    benchmark_cli._print_json(_result("PASS"))
    payload = json.loads(capsys.readouterr().out)
    assert payload["suite_id"] == "harness-v2"
    assert payload["status"] == "PASS"
    assert len(payload["gates"]) == 6
    assert payload["manifest_sha256"] == "a" * 64


def test_main_returns_exit_code_from_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main` 退出码 = 0（PASS）/ 1（FAIL）——CI 门禁的依据。"""

    async def _fake_run(args: object) -> int:
        return 0 if vars(args).get("version") == "v1" else 1

    monkeypatch.setattr(benchmark_cli, "_run", _fake_run)
    assert benchmark_cli.main(["--version", "v1"]) == 0
    assert benchmark_cli.main(["--version", "v2"]) == 1


def test_fail_status_maps_to_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """**禁止「打印 FAIL 却退出 0」**：FAIL 必须返回 1。"""

    async def _fake_run(args: object) -> int:
        return 1

    monkeypatch.setattr(benchmark_cli, "_run", _fake_run)
    assert benchmark_cli.main([]) == 1


def test_keyboard_interrupt_maps_to_130(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl-C → 退出码 130（不冒充 PASS）。"""

    async def _interrupted(args: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(benchmark_cli, "_run", _interrupted)
    assert benchmark_cli.main([]) == 130


async def test_run_leaves_no_residue_in_database(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**CLI 不得污染数据库**（S9 禁止事项）：跑完 `_run` 后事务回滚，零残留。

    回归背景：CLI 曾在开发库提交了 `benchmark-cli` 项目 + 1 条 BenchmarkRun，
    导致 `tests/projects/test_bootstrap.py` 的「空库」断言 409 失败。

    用 `db_session` 夹具把 CLI 的 `engine` 绑定到与其它用例同一条连接/事务，
    避免本用例在夹具 teardown 之后再碰全局 engine（会触发 `Event loop is closed`）。
    """
    from sqlalchemy import func, select

    from etl_agent.infrastructure import database
    from etl_agent.infrastructure.models import BenchmarkRun, Project

    args = benchmark_cli._build_parser().parse_args(["--layers", "L0"])

    # CLI 内部用全局 engine；把它临时指向夹具的连接，走同一条事务生命周期。
    monkeypatch.setattr(database, "engine", db_session.bind)

    async def _counts() -> tuple[int, int]:
        projects = await db_session.scalar(select(func.count()).select_from(Project))
        runs = await db_session.scalar(select(func.count()).select_from(BenchmarkRun))
        return int(projects or 0), int(runs or 0)

    before = await _counts()
    exit_code = await benchmark_cli._run(args)
    after = await _counts()

    assert exit_code == 0, "L0 静态层应全部通过"
    assert after == before, f"CLI 留下了残留数据：{before} -> {after}"
