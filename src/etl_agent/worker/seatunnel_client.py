"""M-K 执行 Worker — SeaTunnel 客户端（REST 提交 + CLI `--check`，FR-20/38）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 2.1 章数据面引擎行 / 第 3.2 章 M-G/M-K /
第 8 章「安全 · 凭据保护」
覆盖 FR：FR-38（调用 SeaTunnel 作业并回传状态与指标）、FR-20（Worker 侧物化运行期
变量后执行引擎 `--check`，只回传脱敏摘要）
对应 AC：AC-02（摘要不含凭据与 HOCON 正文）、AC-04 前置（引擎检查真实化）

两个通道：
1. **REST 提交**：`POST {SEATUNNEL_URL}/submit-job`（JSON `{jobId, jobName, jobContent}`），
   回参 `jobId`；查询 `GET {SEATUNNEL_URL}/job-info/{jobId}`；终止 `POST /stop-job`；
2. **CLI `--check`**：`seatunnel.sh --config <file> --check` 本地子进程
   （临时文件用后即删），回传**脱敏摘要**——只含 ok 与失败分类，不含 HOCON 正文。

**REST 路径口径（实测对齐，见阶段报告「执行偏差」）**：提示词 §8.4 写的是 Hazelcast 路径
`/hazelcast/rest/maps/submit-job`，但对本项目 compose 固定的引擎镜像
`flowpilot/seatunnel:2.3.13` 实测该路径 **404**；真实契约是 SeaTunnel Zeta REST v2 的
`/submit-job` / `/job-info/{jobId}` / `/stop-job`（均已实测：`POST /submit-job` 进入
`JobInfoService.submitJob`，`GET /job-info/{id}` 返回 `{"jobId": "..."}`）。
客户端按**真实引擎**对齐——按提示词写路径只会得到稳定的 404。

注意：本客户端只被 Worker 在物化完成后调用；`--check` 的输入是**物化后** HOCON，
临时文件在进程退出前必须删除（不把明文凭据留在磁盘上，AC-02）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from etl_agent.execution.errors import (
    SeatunnelCheckFailed,
    SeatunnelSubmitFailed,
    SeatunnelUnavailable,
)

# SeaTunnel Zeta REST v2 端点（实测对齐 flowpilot/seatunnel:2.3.13）
SUBMIT_PATH = "/submit-job"
JOB_INFO_PATH = "/job-info"
STOP_PATH = "/stop-job"
# 运行中作业列表（诊断用，不在主链路调用）
RUNNING_JOBS_PATH = "/running-jobs"

# 引擎侧状态字段名（v2 用 `jobStatus`；保留 `status` 兼容旧版）
STATUS_FIELD = "jobStatus"
STATUS_FIELD_LEGACY = "status"

# 引擎侧终态（SeaTunnel JobStatus 枚举的本期用集）
STATUS_FINISHED = "FINISHED"
STATUS_FAILED = "FAILED"
STATUS_CANCELED = "CANCELED"
ENGINE_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {STATUS_FINISHED, STATUS_FAILED, STATUS_CANCELED}
)


@dataclass(frozen=True)
class EngineJobStatus:
    """引擎作业状态（回传仅状态与聚合指标，不含业务数据）。"""

    job_id: str
    status: str
    metrics: dict[str, Any] | None = None
    error: str | None = None


class SeaTunnelClient(Protocol):
    """引擎客户端契约（Worker 侧注入；测试用 Fake 替换 REST 子进程通道）。"""

    async def submit_job(self, hocon: str, *, job_name: str = "") -> str: ...

    async def job_status(self, job_id: str) -> EngineJobStatus: ...

    async def stop_job(self, job_id: str) -> None: ...

    async def check(self, hocon: str) -> dict[str, Any]: ...


class RestSeaTunnelClient:
    """真实 SeaTunnel 客户端（REST 提交 + CLI --check）。"""

    def __init__(
        self,
        base_url: str,
        *,
        submit_timeout_seconds: int = 30,
        check_timeout_seconds: int = 120,
        cli_path: str = "seatunnel.sh",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._submit_timeout = submit_timeout_seconds
        self._check_timeout = check_timeout_seconds
        self._cli_path = cli_path

    async def submit_job(self, hocon: str, *, job_name: str = "") -> str:
        """提交作业（REST v2），返回引擎 JobId。

        `job_name` 只用于引擎侧可读标识（稳定 JobId 便于在引擎 UI 里对账），
        `jobId` 留空由引擎生成——**不把本地幂等标识塞进引擎主键**，避免与
        「引擎侧已存在同 id 作业」的语义纠缠（本地幂等由 Replay Guard + 稳定
        JobId 记录承担，FR-34/42）。

        Raises:
            SeatunnelUnavailable:   连接 / 超时（可重试）。
            SeatunnelSubmitFailed:  引擎拒绝（回传失败分类，不含 HOCON 正文）。
        """
        try:
            async with httpx.AsyncClient(timeout=self._submit_timeout) as client:
                response = await client.post(
                    f"{self._base_url}{SUBMIT_PATH}",
                    json={"jobId": "", "jobName": job_name, "jobContent": hocon},
                )
        except httpx.HTTPError as exc:
            raise SeatunnelUnavailable(f"SeaTunnel 不可达：{type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise SeatunnelSubmitFailed(
                f"SeaTunnel 拒绝提交（HTTP {response.status_code}）：{_brief(response.text)}"
            )
        job_id = _extract_job_id(response)
        if not job_id:
            raise SeatunnelSubmitFailed("SeaTunnel 提交响应缺少 jobId")
        return job_id

    async def job_status(self, job_id: str) -> EngineJobStatus:
        """查询作业状态（REST v2 `GET /job-info/{jobId}`）。

        Raises:
            SeatunnelUnavailable:  引擎不可达 / 查询失败。
        """
        try:
            async with httpx.AsyncClient(timeout=self._submit_timeout) as client:
                response = await client.get(f"{self._base_url}{JOB_INFO_PATH}/{job_id}")
        except httpx.HTTPError as exc:
            raise SeatunnelUnavailable(f"SeaTunnel 不可达：{type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise SeatunnelUnavailable(
                f"SeaTunnel 查询失败（HTTP {response.status_code}）：{_brief(response.text)}"
            )
        return _parse_job_info(response.json())

    async def stop_job(self, job_id: str) -> None:
        """终止作业（REST v2 `POST /stop-job`，取消链路调用，FR-49）。

        Raises:
            SeatunnelUnavailable:  引擎不可达 / 终止失败。
        """
        try:
            async with httpx.AsyncClient(timeout=self._submit_timeout) as client:
                response = await client.post(
                    f"{self._base_url}{STOP_PATH}", json={"jobId": job_id}
                )
        except httpx.HTTPError as exc:
            raise SeatunnelUnavailable(f"SeaTunnel 不可达：{type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise SeatunnelUnavailable(
                f"SeaTunnel 终止失败（HTTP {response.status_code}）：{_brief(response.text)}"
            )

    async def check(self, hocon: str) -> dict[str, Any]:
        """引擎侧配置校验（CLI `--check`，FR-20）。

        物化后的 HOCON 写入临时文件（600 权限）→ 子进程 `--check` → 删除临时文件 →
        只回传 `{"ok": bool, "summary": str}` 脱敏摘要（不含 HOCON 正文与凭据）。

        Raises:
            SeatunnelCheckFailed: 校验未通过或 CLI 不可用。
        """
        if not shutil.which(self._cli_path):
            raise SeatunnelCheckFailed(f"SeaTunnel CLI 不可用：{self._cli_path} 不在 PATH")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".conf", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(hocon)
            temp_path = handle.name
        try:
            os.chmod(temp_path, 0o600)  # 物化正文含真实凭据：仅属主可读（AC-02）
            process = await asyncio.create_subprocess_exec(
                self._cli_path,
                "--config",
                temp_path,
                "--check",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self._check_timeout
                )
            except TimeoutError as exc:
                process.kill()
                raise SeatunnelCheckFailed(
                    f"SeaTunnel --check 超时（{self._check_timeout}s）"
                ) from exc
        finally:
            # 无论成败，含明文凭据的临时文件必须删除（AC-02）
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        if process.returncode == 0:
            return {"ok": True, "summary": "engine check passed"}
        return {
            "ok": False,
            "summary": _brief(stderr.decode("utf-8", errors="replace")),
        }


def _brief(text: str, limit: int = 200) -> str:
    """错误摘要截断（只回传失败分类，不回传 HOCON 正文 / 凭据，AC-02）。"""
    collapsed = " ".join(text.split())
    return collapsed[:limit]


def _extract_job_id(response: httpx.Response) -> str | None:
    """从 submit-job 响应提取 jobId（SeaTunnel 回传 `{"jobId": "...", "jobName": "..."}`）。"""
    try:
        payload: Any = response.json()
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        value = payload.get("jobId")
        return str(value) if value is not None else None
    return None


def _parse_job_info(payload: Any) -> EngineJobStatus:
    """解析 job-info 响应（只取状态与聚合指标，不含业务数据）。

    v2 的状态字段名是 `jobStatus`；缺失时回落到 `status`（旧版 / 精简响应），
    两者都缺失即 `UNKNOWN`——**不猜**成 RUNNING 或 FINISHED（猜错会误判终态）。
    """
    if not isinstance(payload, dict):
        return EngineJobStatus(job_id="", status="UNKNOWN")
    job_id = str(payload.get("jobId", ""))
    raw_status = payload.get(STATUS_FIELD)
    if raw_status is None:
        raw_status = payload.get(STATUS_FIELD_LEGACY)
    status = str(raw_status) if raw_status is not None else "UNKNOWN"
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else None
    raw_error = payload.get("errorMessage")
    if raw_error is None:
        raw_error = payload.get("error")
    error = str(raw_error) if raw_error is not None else None
    return EngineJobStatus(job_id=job_id, status=status, metrics=metrics, error=error)


# ---------------------------------------------------------------------------
# MockSeaTunnelClient（F5：S10 演示口径 mock engine，人类裁决 C）
#
# 背景：裁决 C 的「mock engine」此前只在 tests/helpers.py 有替身，生产 worker 的
# `_run()` 硬编码 RestSeaTunnelClient——容器内既无 seatunnel.sh（CLI --check 必败），
# REST submit 又撞 T-ENGINE-01 的 NPE，演示链路走不到提交之后。本类把演示态引擎
# 通道落到 src 侧，由 `engine_mode=mock` 选型（config.py 白名单 + 生产禁用）。
#
# 语义边界（不可扩张）：
# - **只 mock 引擎交互本身**（check / submit / status）：返回演示态合成指标；
# - 不搬数据：Doris 源/目标表不会被本类写入，质量契约/监督/回滚判定消费的是
#   合成指标（脚本 [MOCK] 标注必须如实反映本模式，见 demo-scenario-a.ps1）；
# - 指标形态与真实 seed 对齐（1002 行 / 2 脏行：NULL 金额、水位越界），
#   换取质量分流四态可演示，不伪装成真实引擎结果。
# ---------------------------------------------------------------------------
class MockSeaTunnelClient:
    """演示态引擎客户端（engine_mode=mock）：check/submit/status 全合成。

    与 `tests/helpers.py::FakeSeaTunnelClient` 的分工：后者是单测的脚本化替身
    （可按状态序列回放），本类是**生产可装配**的演示态通道（`build_engine` 选型），
    测试镜像不含 tests/ 目录，二者不能互相替代。
    """

    # 稳定 job_id 前缀（对齐稳定 JobId 语义：同版本重复提交同一 id）
    JOB_ID_PREFIX = "mock-job-"

    async def check(self, hocon: str) -> dict[str, Any]:
        """引擎校验：mock 直接通过（不探测 CLI、不起子进程——容器内 CLI 不可用）。"""
        return {"ok": True, "summary": "engine check passed (mock)"}

    async def submit_job(self, hocon: str, *, job_name: str = "") -> str:
        """提交：不起 REST，返回稳定 mock job_id（幂等可重放）。"""
        return f"{self.JOB_ID_PREFIX}{job_name or 'unnamed'}"

    async def job_status(self, job_id: str) -> EngineJobStatus:
        """状态：恒 FINISHED + 演示态聚合指标（与真实 seed 1002 行 / 2 脏行同形态）。"""
        return EngineJobStatus(
            job_id=job_id,
            status=STATUS_FINISHED,
            metrics=self.demo_metrics(),
        )

    async def stop_job(self, job_id: str) -> None:
        """终止：no-op（mock 通道没有真实作业可停）。"""

    @staticmethod
    def demo_metrics() -> dict[str, Any]:
        """演示态聚合指标（引擎回传口径：行数 / 字节 / 质量分流事实）。

        脏行两条：NULL 金额、updated_at 越界（T-DEMO-01 现状下地址超宽行
        已不可落库）。reject_rate≈0.2%，低于典型质量契约阈值，四态走 PASS。
        """
        return {
            "source_rows": 1002,
            "sink_rows": 1000,
            "source_bytes": 102_400,
            "sink_bytes": 101_200,
            "tables": [{"table": "order_wide", "rows": 1000, "bytes": 101_200}],
            "quality": {
                "total_rows": 1002,
                "passed_rows": 1000,
                "rejected_rows": 2,
                "error_codes": {
                    "QUALITY_NOT_NULL_AMOUNT": 1,
                    "QUALITY_WATERMARK_OUT_OF_RANGE": 1,
                },
            },
        }


# ---------------------------------------------------------------------------
# CliSeaTunnelClient（T-ENGINE-01：真实引擎链路，CLI 提交通道）
#
# 背景：ENGINE 侧 REST `POST /submit-job` 在 flowpilot / apache 2.3.8~2.3.13
# **全版本** `ConfigShadeUtils.processConfig` NPE（T-ENGINE-01，request body /
# 镜像 / 版本均已排除）——只走 REST 提交无法闭环 AC-16。
#
# 引擎实测（2026-10-16，flowpilot/seatunnel:2.3.13）：
# - CLI `seatunnel.sh --config <f> --async`：提交到本机 Hazelcast 集群（默认 -m cluster），
#   约 2s 返回，stdout 含 `job id: <id>`；`--check` 校验（exit 0 静默）；
#   `--cancel-job <id>` 取消；
# - REST `GET /job-info/{jobId}` 对 **CLI 提交的作业同样有效**（返回 jobStatus/jobDag）。
#
# 故本类的通道分工：check / submit / stop 走 CLI（本地子进程），job_status 复用
# RestSeaTunnelClient 的既有解析（组合，不复制逻辑）。
#
# 依赖（worker 镜像，infra/Dockerfile.worker）：/opt/seatunnel（CLI + 引擎客户端，
# connectors 白名单 jdbc/doris/console/fake）+ JDK8；集群成员配置指向 compose
# 服务名（hazelcast-client.worker.yaml → seatunnel:5801）。
# ---------------------------------------------------------------------------
_CLI_JOB_ID_PATTERN = re.compile(r"job id:\s*(\d+)")

# CLI 子进程替身（测试注入）：(args, timeout) → (exit_code, stdout)
CliRunner = Callable[[list[str], int], Awaitable[tuple[int, str]]]


def _hocon_tempfile(hocon: str) -> str:
    """物化 HOCON 落 0600 临时文件，返回路径（调用方负责用后即删，AC-02）。"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".conf", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(hocon)
        temp_path = handle.name
    os.chmod(temp_path, 0o600)  # 物化正文含真实凭据：仅属主可读（AC-02）
    return temp_path


class CliSeaTunnelClient:
    """真实引擎客户端：CLI 提交 + REST 状态（T-ENGINE-01）。

    runner 为测试注入点（默认 None = 真实子进程）：签名
    `async runner(args: list[str], timeout: int) -> tuple[int, str]`，
    回传 (exit_code, stdout)。测试用伪 runner 即可覆盖全部路径，无需真引擎。
    """

    def __init__(
        self,
        base_url: str,
        *,
        cli_path: str = "/opt/seatunnel/bin/seatunnel.sh",
        java_home: str = "/usr/local/openjdk-8",
        submit_timeout_seconds: int = 180,
        check_timeout_seconds: int = 180,
        runner: CliRunner | None = None,
    ) -> None:
        # 状态查询复用 REST 客户端（对 CLI 提交的作业实测有效；仅 submit 是 NPE）
        self._status_client = RestSeaTunnelClient(
            base_url,
            submit_timeout_seconds=submit_timeout_seconds,
            check_timeout_seconds=check_timeout_seconds,
        )
        self._cli_path = cli_path
        self._java_home = java_home
        self._submit_timeout = submit_timeout_seconds
        self._check_timeout = check_timeout_seconds
        self._runner = runner

    async def _run_cli(self, args: list[str], timeout: int) -> tuple[int, str]:
        """执行 CLI（子进程 env 注入 Java PATH；runner 注入时走测试替身）。"""
        if self._runner is not None:
            return await self._runner(args, timeout)
        if not os.path.exists(self._cli_path):
            raise SeatunnelUnavailable(
                f"SeaTunnel CLI 不可用：{self._cli_path} 不存在"
                "（worker 镜像应含引擎客户端层，见 infra/Dockerfile.worker）"
            )
        env = os.environ.copy()
        java_bin = Path(self._java_home, "bin")
        if java_bin.is_dir():
            env["PATH"] = str(java_bin) + os.pathsep + env.get("PATH", "")
            env["JAVA_HOME"] = self._java_home
        try:
            process = await asyncio.create_subprocess_exec(
                self._cli_path,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except TimeoutError:
                process.kill()
                raise SeatunnelUnavailable(f"SeaTunnel CLI 超时（{timeout}s）") from None
        except OSError as exc:
            raise SeatunnelUnavailable(f"SeaTunnel CLI 启动失败：{type(exc).__name__}") from exc
        return process.returncode or 0, stdout.decode("utf-8", errors="replace")

    async def check(self, hocon: str) -> dict[str, Any]:
        """引擎侧配置校验：CLI `--check`（exit 0 = 通过；失败回脱敏摘要，AC-02）。"""
        temp_path = _hocon_tempfile(hocon)
        try:
            returncode, output = await self._run_cli(
                ["--config", temp_path, "--check"], self._check_timeout
            )
        finally:
            _remove_tempfile(temp_path)
        if returncode == 0:
            return {"ok": True, "summary": "engine check passed"}
        return {"ok": False, "summary": _brief(output)}

    async def submit_job(self, hocon: str, *, job_name: str = "") -> str:
        """提交作业：CLI `--async`（提交后客户端即退出），从 stdout 解析 job id。

        job_name 仅用于错误信息定位——CLI 无命名参数（引擎侧作业名固定
        SeaTunnel_Job），本地幂等由 Replay Guard 与稳定 JobId 记录承担（FR-34）。
        """
        temp_path = _hocon_tempfile(hocon)
        try:
            returncode, output = await self._run_cli(
                ["--config", temp_path, "--async"], self._submit_timeout
            )
        finally:
            _remove_tempfile(temp_path)
        if returncode != 0:
            raise SeatunnelSubmitFailed(
                f"SeaTunnel CLI 提交失败（exit {returncode}）：{_brief(output)}"
            )
        match = _CLI_JOB_ID_PATTERN.search(output)
        if match is None:
            raise SeatunnelSubmitFailed(
                f"SeaTunnel CLI 提交输出缺少 job id（job_name={job_name or 'unnamed'}）：{_brief(output)}"
            )
        return match.group(1)

    async def job_status(self, job_id: str) -> EngineJobStatus:
        """查询作业状态：复用 REST `/job-info`（对 CLI 提交的作业实测有效）。"""
        return await self._status_client.job_status(job_id)

    async def stop_job(self, job_id: str) -> None:
        """取消作业：CLI `--cancel-job`（与提交通道同源，避免再踩 REST 未验证路径）。"""
        returncode, output = await self._run_cli(["--cancel-job", job_id], self._submit_timeout)
        if returncode != 0:
            raise SeatunnelUnavailable(
                f"SeaTunnel CLI 取消失败（exit {returncode}）：{_brief(output)}"
            )


def _remove_tempfile(path: str) -> None:
    """删除临时文件（含明文的 HOCON，用后即删，AC-02）。"""
    try:
        os.unlink(path)
    except OSError:
        pass


def build_engine(settings: Any) -> SeaTunnelClient:
    """按引擎选型装配客户端（engine_mode 白名单在 config 侧 fail-fast）。

    Args:
        settings: 应用 Settings（读取 engine_mode / seatunnel_url / 超时）。

    Returns:
        engine_mode=mock → MockSeaTunnelClient；real-cli → CliSeaTunnelClient
        （CLI 提交 + REST 状态，T-ENGINE-01）；real → RestSeaTunnelClient。
        非法值已在 `Settings.validate_worker` 启动期拒绝，此处不再重复校验。
    """
    if settings.engine_mode == "mock":
        return MockSeaTunnelClient()
    if settings.engine_mode == "real-cli":
        return CliSeaTunnelClient(
            settings.seatunnel_url,
            submit_timeout_seconds=settings.seatunnel_submit_timeout_seconds,
            check_timeout_seconds=settings.seatunnel_check_timeout_seconds,
        )
    return RestSeaTunnelClient(
        settings.seatunnel_url,
        submit_timeout_seconds=settings.seatunnel_submit_timeout_seconds,
        check_timeout_seconds=settings.seatunnel_check_timeout_seconds,
    )
