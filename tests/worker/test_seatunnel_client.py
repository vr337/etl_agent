"""SeaTunnel 客户端用例（T-13.7 / FR-20/38）。

覆盖：REST 提交与状态查询、失败分类（拒绝 / 不可达）、CLI `--check` 脱敏摘要与
临时文件（含明文凭据）用后即删；MockSeaTunnelClient（S10 演示口径：人类裁决 C
mock engine，F5）与 build_engine 选型（engine_mode 白名单）。
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from etl_agent.config import ProductionConfigError
from etl_agent.execution.errors import (
    SeatunnelCheckFailed,
    SeatunnelSubmitFailed,
    SeatunnelUnavailable,
)
from etl_agent.worker import seatunnel_client as module
from etl_agent.worker.seatunnel_client import (
    ENGINE_TERMINAL_STATUSES,
    RestSeaTunnelClient,
)
from tests.helpers import make_settings

_HOCON_WITH_SECRET = 'sink { Jdbc { password = "super-secret-value" } }'


def _patch_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """把模块内新建的 httpx.AsyncClient 换成 MockTransport 客户端。"""
    real_client = httpx.AsyncClient

    def _factory(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


async def test_submit_job_returns_job_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """REST 提交成功返回引擎 JobId（FR-38），请求体为 v2 的 {jobId, jobName, jobContent}。"""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        body = json.loads(request.content)
        seen["body"] = body
        return httpx.Response(200, json={"jobId": "job-42", "jobName": "etl-job-1"})

    _patch_transport(monkeypatch, handler)
    client = RestSeaTunnelClient("http://engine:8080")

    assert await client.submit_job("hocon-body", job_name="etl-job-1") == "job-42"
    # 路径必须与真实引擎对齐（实测 flowpilot/seatunnel:2.3.13 的 Zeta REST v2）
    assert seen["path"] == "/submit-job"
    assert seen["body"] == {
        "jobId": "",
        "jobName": "etl-job-1",
        "jobContent": "hocon-body",
    }


def test_rest_paths_match_real_engine() -> None:
    """端点常量与真实引擎一致（提示词 §8.4 的 Hazelcast 路径实测 404，已按实测对齐）。"""
    assert module.SUBMIT_PATH == "/submit-job"
    assert module.JOB_INFO_PATH == "/job-info"
    assert module.STOP_PATH == "/stop-job"
    assert "hazelcast" not in module.SUBMIT_PATH


async def test_submit_job_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """引擎拒绝提交 → `BIZ_422_SEATUNNEL_SUBMIT_FAILED`（摘要脱敏）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad config: password=leaked")

    _patch_transport(monkeypatch, handler)
    client = RestSeaTunnelClient("http://engine:8080")
    with pytest.raises(SeatunnelSubmitFailed) as excinfo:
        await client.submit_job("hocon-body")
    assert "HTTP 400" in str(excinfo.value)


async def test_submit_job_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """连接失败 → `BIZ_503_SEATUNNEL_UNAVAILABLE`（可重试类）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _patch_transport(monkeypatch, handler)
    client = RestSeaTunnelClient("http://engine:8080")
    with pytest.raises(SeatunnelUnavailable):
        await client.submit_job("hocon-body")


async def test_job_status_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    """状态查询只解析状态与聚合指标（v2 字段名为 `jobStatus`）。"""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "jobId": "job-1",
                "jobStatus": "RUNNING",
                "metrics": {"source_rows": 5},
            },
        )

    _patch_transport(monkeypatch, handler)
    client = RestSeaTunnelClient("http://engine:8080")
    status = await client.job_status("job-1")

    assert seen["path"] == "/job-info/job-1"
    assert status.status == "RUNNING"
    assert status.metrics == {"source_rows": 5}
    assert "FINISHED" in ENGINE_TERMINAL_STATUSES


async def test_job_status_unknown_when_field_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """引擎未回传状态字段（如作业不存在）→ UNKNOWN，**不猜**成 RUNNING/FINISHED。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobId": "job-missing"})

    _patch_transport(monkeypatch, handler)
    client = RestSeaTunnelClient("http://engine:8080")
    status = await client.job_status("job-missing")
    assert status.status == "UNKNOWN"


async def test_job_status_accepts_legacy_status_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧版 / 精简响应用 `status` 字段也能识别（向后兼容，不误判）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobId": "j", "status": "FINISHED"})

    _patch_transport(monkeypatch, handler)
    client = RestSeaTunnelClient("http://engine:8080")
    assert (await client.job_status("j")).status == "FINISHED"


async def test_stop_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """终止作业走 `/stop-job` 且以请求体传 jobId（v2 契约，取消链路前置）。"""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text="ok")

    _patch_transport(monkeypatch, handler)
    client = RestSeaTunnelClient("http://engine:8080")
    await client.stop_job("job-9")

    assert seen["path"] == "/stop-job"
    assert seen["body"] == {"jobId": "job-9"}


class _FakeProcess:
    """伪子进程：按脚本回传 returncode 与 stderr。"""

    def __init__(self, returncode: int, stderr: bytes) -> None:
        self.returncode = returncode
        self._stderr = stderr
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr

    def kill(self) -> None:
        self.killed = True


def _patch_cli(
    monkeypatch: pytest.MonkeyPatch, process: _FakeProcess
) -> dict[str, str]:
    """把 CLI 探测与子进程启动替换为伪实现，并记录临时文件路径。"""
    captured: dict[str, str] = {}

    monkeypatch.setattr(module.shutil, "which", lambda name: f"/usr/local/bin/{name}")

    async def _spawn(*args: str, **kwargs: Any) -> _FakeProcess:
        captured["config_path"] = args[2]  # (cli, "--config", <path>, "--check")
        captured["args"] = " ".join(args)
        # 在子进程启动瞬间记录权限位（用例结束前文件已被删除，事后 stat 拿不到）
        captured["mode"] = (
            oct(os.stat(args[2]).st_mode & 0o777) if os.path.exists(args[2]) else ""
        )
        captured["content"] = (
            Path(args[2]).read_text(encoding="utf-8") if os.path.exists(args[2]) else ""
        )
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
    return captured


async def test_check_returns_sanitized_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--check` 通过：回传脱敏摘要，且含明文的临时文件被删除（AC-02）。"""
    captured = _patch_cli(monkeypatch, _FakeProcess(0, b""))
    client = RestSeaTunnelClient("http://engine:8080")

    result = await client.check(_HOCON_WITH_SECRET)

    assert result == {"ok": True, "summary": "engine check passed"}
    assert captured["args"].endswith("--check")
    # 临时文件（写入的是含明文凭据的物化 HOCON）必须已被删除
    assert not Path(captured["config_path"]).exists()


async def test_check_failure_summary_is_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--check` 失败：摘要截断，且不含 HOCON 正文（只回失败分类）。"""
    _patch_cli(monkeypatch, _FakeProcess(1, b"line1\nline2  " + b"x" * 500))
    client = RestSeaTunnelClient("http://engine:8080")

    result = await client.check(_HOCON_WITH_SECRET)

    assert result["ok"] is False
    summary = str(result["summary"])
    assert len(summary) <= 200
    assert "super-secret-value" not in summary


async def test_check_without_cli_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI 不在 PATH → 校验失败（不静默放过）。"""
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    client = RestSeaTunnelClient("http://engine:8080")
    with pytest.raises(SeatunnelCheckFailed):
        await client.check(_HOCON_WITH_SECRET)


async def test_check_temp_file_mode_restricted(monkeypatch: pytest.MonkeyPatch) -> None:
    """临时文件权限收窄（非 Windows 校验 0600；Windows 无 POSIX 权限位）。"""
    captured = _patch_cli(monkeypatch, _FakeProcess(0, b""))
    client = RestSeaTunnelClient("http://engine:8080")
    await client.check(_HOCON_WITH_SECRET)
    # 写入子进程的正文必须是物化后的 HOCON（含明文），但文件随即被删除
    assert "super-secret-value" in captured["content"]
    assert not Path(captured["config_path"]).exists()
    if os.name != "nt":
        assert captured["mode"] == "0o600"


# ---------------------------------------------------------------------------
# MockSeaTunnelClient（F5：S10 演示口径 mock engine，人类裁决 C）
# 容器 worker 无 seatunnel.sh（F5 实证），且 REST submit 撞 T-ENGINE-01 NPE——
# 裁决 C 的「mock engine」此前只在 tests/helpers.py 有替身、生产从未接线，
# 本类把演示态引擎通道落到 src 侧，check/submit 为 mock、其余链路全真实执行。
# ---------------------------------------------------------------------------


async def test_mock_client_check_ok() -> None:
    """mock check 直接通过：不探测 CLI、不起子进程（F5：容器内 CLI 不可用）。"""
    client = module.MockSeaTunnelClient()
    assert await client.check("hocon-body") == {"ok": True, "summary": "engine check passed (mock)"}


async def test_mock_client_submit_returns_stable_job_id() -> None:
    """mock submit 返回稳定 job_id（同版本重复提交同一 id，对齐稳定 JobId 语义）。"""
    client = module.MockSeaTunnelClient()
    job_a = await client.submit_job("hocon-body", job_name="etl-job-1")
    job_b = await client.submit_job("hocon-body", job_name="etl-job-1")
    assert job_a == job_b
    assert job_a.startswith("mock-job-")


async def test_mock_client_status_finished_with_demo_metrics() -> None:
    """mock 终态 FINISHED + 演示态指标：与真实 seed 形态一致（1002 行 / 2 脏行）。

    脏行两条：NULL 金额、updated_at 越界（T-DEMO-01 现状：地址超宽行已不可落库）。
    mock 不搬数据——Doris 表不会被写入，指标是演示态合成值（脚本 [MOCK] 标注来源）。
    """
    client = module.MockSeaTunnelClient()
    status = await client.job_status("mock-job-1")

    assert status.status == "FINISHED"
    assert status.status in ENGINE_TERMINAL_STATUSES
    metrics = status.metrics or {}
    assert metrics["source_rows"] == 1002
    assert metrics["sink_rows"] == 1000
    quality = metrics["quality"]
    assert (quality["total_rows"], quality["passed_rows"], quality["rejected_rows"]) == (1002, 1000, 2)
    assert quality["error_codes"] == {
        "QUALITY_NOT_NULL_AMOUNT": 1,
        "QUALITY_WATERMARK_OUT_OF_RANGE": 1,
    }


async def test_mock_client_stop_job_is_noop() -> None:
    """mock 终止作业：no-op 不抛（取消链路容错）。"""
    client = module.MockSeaTunnelClient()
    await client.stop_job("mock-job-1")


def test_build_engine_real_by_default() -> None:
    """缺省 engine_mode=real：生产安全默认，绝不因漏配静默降级为 mock。"""
    engine = module.build_engine(make_settings())
    assert isinstance(engine, RestSeaTunnelClient)


def test_build_engine_mock_when_configured() -> None:
    """engine_mode=mock：返回 mock 客户端（S10 演示 / 无引擎环境联调）。"""
    engine = module.build_engine(make_settings(engine_mode="mock"))
    assert isinstance(engine, module.MockSeaTunnelClient)


def test_engine_mode_mock_banned_in_production() -> None:
    """ENV=prod 禁止 engine_mode=mock（对齐 llm_provider 红线：生产拒绝 mock 引擎）。"""
    settings = make_settings(env="prod", engine_mode="mock")
    with pytest.raises(ProductionConfigError, match="ENGINE_MODE"):
        settings.validate_worker()


def test_engine_mode_whitelist() -> None:
    """engine_mode 白名单：非法值拒绝启动（fail-fast，不允许运行期猜）。"""
    settings = make_settings(engine_mode="turbo")
    with pytest.raises(ProductionConfigError, match="ENGINE_MODE"):
        settings.validate_worker()
