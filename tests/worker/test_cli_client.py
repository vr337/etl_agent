"""CliSeaTunnelClient 用例（T-ENGINE-01：SeaTunnel CLI 提交通道）。

覆盖 check / submit（--async 解析 job id）/ status（REST 委派）/ stop（--cancel-job）
四条通道，以及临时文件用后即删（AC-02）。runner 注入点为测试替身（不起真子进程、
不依赖引擎可用性——与 test_seatunnel_client.py 的 MockTransport 同思路）。
"""
from __future__ import annotations

import os

import httpx
import pytest

from etl_agent.execution.errors import SeatunnelSubmitFailed, SeatunnelUnavailable
from etl_agent.worker.seatunnel_client import CliSeaTunnelClient
from tests.helpers import make_settings
from tests.worker.test_seatunnel_client import _patch_transport

_CLI_SUBMIT_OUTPUT = (
    "2026-10-16 08:33:51,558 INFO  [o.a.s.e.c.j.ClientJobProxy] [main] - "
    "Start submit job, job id: 1163394363116486657, with plugin jar "
    "[file:/opt/seatunnel/connectors/connector-jdbc-2.3.13.jar]\n"
    "2026-10-16 08:33:51,572 INFO  Submit job finished, job id: 1163394363116486657\n"
)


def _fake_runner(scripted: list[tuple[int, str]]):
    """伪 CLI：按脚本回传 (exit_code, output)，并记录收到的参数。"""
    calls: list[list[str]] = []

    async def _run(args: list[str], timeout: int) -> tuple[int, str]:
        calls.append(list(args))
        return scripted.pop(0) if scripted else (0, "")

    return _run, calls


def _client(scripted: list[tuple[int, str]], **overrides: object) -> tuple[CliSeaTunnelClient, list[list[str]]]:
    runner, calls = _fake_runner(scripted)
    return CliSeaTunnelClient("http://engine:8080", runner=runner, **overrides), calls


async def test_check_ok_and_tempfile_removed() -> None:
    """--check 通过（exit 0 静默）→ ok；含明文 HOCON 的临时文件用后即删（AC-02）。"""
    client, calls = _client([(0, "")])
    assert await client.check("hocon-with-secret") == {"ok": True, "summary": "engine check passed"}
    args = calls[0]
    assert args[0] == "--config" and args[-1] == "--check"
    assert not os.path.exists(args[1])


async def test_check_failure_returns_summary() -> None:
    """--check 失败：exit 非 0 → ok=False + 截断摘要（不含 HOCON 正文）。"""
    client, _ = _client([(1, "error: bad config\nline2")])
    result = await client.check("hocon")
    assert result["ok"] is False
    assert "bad config" in result["summary"] and "line2" in result["summary"]
    assert len(result["summary"]) <= 200


async def test_submit_async_parses_job_id() -> None:
    """--async 提交：2s 风格输出中解析 job id（实测格式），且临场文件已删。"""
    client, calls = _client([(0, _CLI_SUBMIT_OUTPUT)])
    job_id = await client.submit_job("hocon", job_name="etl-job-1")
    assert job_id == "1163394363116486657"
    assert calls[0][0] == "--config" and calls[0][-1] == "--async"
    assert not os.path.exists(calls[0][1])


async def test_submit_without_job_id_raises() -> None:
    """CLI 提交成功退出但输出无 job id → 提交失败（不猜 id）。"""
    client, _ = _client([(0, "submitted successfully")])
    with pytest.raises(SeatunnelSubmitFailed, match="job id"):
        await client.submit_job("hocon")


async def test_submit_nonzero_exit_raises() -> None:
    """CLI 提交非零退出 → SeatunnelSubmitFailed（附截断输出）。"""
    client, _ = _client([(2, "Error: cluster unreachable")])
    with pytest.raises(SeatunnelSubmitFailed, match="cluster unreachable"):
        await client.submit_job("hocon")


async def test_stop_job_calls_cancel_job() -> None:
    """取消：CLI --cancel-job <jobId>（与提交通道同源）。"""
    client, calls = _client([(0, "cancel ok")])
    await client.stop_job("job-42")
    assert calls[0][0] == "--cancel-job" and calls[0][1] == "job-42"


async def test_stop_job_nonzero_raises() -> None:
    client, _ = _client([(1, "job not found")])
    with pytest.raises(SeatunnelUnavailable):
        await client.stop_job("job-42")


async def test_job_status_delegates_to_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    """状态查询复用 REST /job-info（对 CLI 提交的作业实测有效）。"""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"jobId": "job-9", "jobStatus": "FINISHED", "metrics": {"sink_rows": 4}})

    _patch_transport(monkeypatch, handler)
    client = CliSeaTunnelClient("http://engine:8080")
    status = await client.job_status("job-9")
    assert seen["path"] == "/job-info/job-9"
    assert status.status == "FINISHED"
    assert status.metrics == {"sink_rows": 4}


async def test_missing_cli_raises_unavailable() -> None:
    """CLI 不存在（非注入 runner）→ SeatunnelUnavailable（提示补引擎客户端层）。"""
    client = CliSeaTunnelClient("http://engine:8080", cli_path="/nonexistent/seatunnel.sh")
    with pytest.raises(SeatunnelUnavailable, match="Dockerfile.worker"):
        await client.check("hocon")


def test_build_engine_real_cli_selects_cli_client() -> None:
    """build_engine：engine_mode=real-cli → CliSeaTunnelClient。"""
    from etl_agent.worker import seatunnel_client as module

    engine = module.build_engine(make_settings(engine_mode="real-cli"))
    assert isinstance(engine, module.CliSeaTunnelClient)


def test_engine_mode_real_cli_allowed_in_production() -> None:
    """real-cli 是生产合法模式（prod 只禁 mock）。"""
    settings = make_settings(env="prod", engine_mode="real-cli")
    settings.validate_worker()
