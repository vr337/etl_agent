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
import shutil
import tempfile
from dataclasses import dataclass
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
