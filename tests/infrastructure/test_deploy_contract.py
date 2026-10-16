"""部署契约静态断言（F4 防回归：Dockerfile COPY 覆盖 + 契约文件存在）。

F4 背景（2026-10-16 联调实证）：`infra/Dockerfile.api` 漏 COPY `configs/`，
容器 Worker 启动即败于 `CredentialMaterializationFailed`（占位符契约
`configs/credential_placeholders.yaml` 不在 `/app` 下）——生产镜像的运行时
文件集此前无任何守卫。本组用例把「镜像必须自带哪些文件」固化为静态断言：

1. Dockerfile 的 COPY 指令必须覆盖 src/ alembic/ alembic.ini configs/；
2. 关键运行时文件在仓库中真实存在（占位符契约 + 策略包清单/签名 + alembic.ini）；
3. configs/ 下的契约文件名与 `credential_materialization.PLACEHOLDER_CONTRACT_PATH`
   声明一致（防文件改名后镜像与代码各说各话）。

不构建镜像（CI 无 Docker 时也能跑）——COPY 覆盖面与文件存在性是构建期
 Dios 事实，静态解析足够拦截同类回归。
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "infra" / "Dockerfile.api"

# 镜像运行必需的文件集（漏一个即特定运行期故障，逐项对应一次真实事故或已知依赖）
REQUIRED_COPIES = ("src/", "alembic/", "alembic.ini", "configs/")


def _copy_sources() -> list[str]:
    """解析 Dockerfile 的 COPY 行，回传源路径列表（跳过 COPY --from 多阶段行）。"""
    sources: list[str] = []
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("COPY ") or stripped.startswith("COPY --from"):
            continue
        # COPY <src> <dst>——取第一段为源（本仓 Dockerfile 均为单源单目标）
        parts = stripped.split()
        if len(parts) >= 3:
            sources.append(parts[1])
    return sources


def test_dockerfile_covers_required_paths() -> None:
    """F4 防回归：镜像 COPY 必须覆盖运行必需文件集（曾漏 configs/ 致执行必败）。"""
    sources = _copy_sources()
    missing = [item for item in REQUIRED_COPIES if item not in sources]
    assert not missing, f"Dockerfile.api 漏 COPY：{missing}（当前 COPY 源：{sources}）"


def test_required_runtime_files_exist_in_repo() -> None:
    """契约/策略/迁移配置文件在仓库中存在（COPY 得到才有意义）。"""
    for relative in (
        "configs/credential_placeholders.yaml",
        "alembic.ini",
        "alembic/env.py",
    ):
        assert (REPO_ROOT / relative).is_file(), f"仓库缺少运行时文件：{relative}"


def test_placeholder_contract_path_matches_code() -> None:
    """代码声明的契约路径与仓库文件一致（防改名后双重漂移）。

    `credential_materialization.PLACEHOLDER_CONTRACT_PATH` 是相对路径，
    按 WORKDIR /app 解析；Dockerfile COPY configs/ 后该文件必须落在
    `/app/configs/credential_placeholders.yaml`。
    """
    from etl_agent.worker.credential_materialization import PLACEHOLDER_CONTRACT_PATH

    declared = Path(PLACEHOLDER_CONTRACT_PATH).as_posix()
    assert (REPO_ROOT / declared).is_file(), (
        f"代码声明的契约路径 {declared} 在仓库不存在"
        "（改名需同步 Dockerfile COPY 与 worker 运行目录）"
    )
    assert declared.startswith("configs/"), "契约必须位于 configs/ 下（COPY 覆盖面）"


def test_policy_package_files_exist() -> None:
    """策略包清单与签名在仓库存在（Harness 装配前置，路径见 config.py 缺省值）。"""
    from etl_agent.config import settings

    assert (REPO_ROOT / settings.policy_package_path).is_file()
    assert (REPO_ROOT / settings.policy_package_signature_path).is_file()


def test_dockerfile_declares_configs_before_runtime_layers() -> None:
    """COPY 顺序卫生：configs/ 与源码同段（缓存失效语义一致，避免半新镜像）。

    并非功能断言——把 configs/  COPY 从「依赖层之后」误移到「CMD 之后」不会
    改变功能，但会让缓存行为难以推理；此处仅锁定与既有 COPY 同段的位置关系。
    """
    text_content = DOCKERFILE.read_text(encoding="utf-8")
    lines = [line.strip() for line in text_content.splitlines()]
    copy_lines = [line for line in lines if line.startswith("COPY ") and not line.startswith("COPY --from")]
    # configs/ 应与 src/ 相邻（同段），不允许孤立出现在依赖层之前（无 env 可吃）
    indexes = {parts[1]: i for i, line in enumerate(copy_lines) if len(parts := line.split()) >= 3}
    assert indexes.get("configs/", -1) >= indexes.get("src/", 0) - 1, (
        "configs/ 应与 src/ 同段 COPY（保持缓存语义直观）"
    )


def test_compose_worker_engine_mode_is_explicit() -> None:
    """compose worker 显式声明 ETL_AGENT_ENGINE_MODE（演示口径可审，不靠默认）。"""
    compose = (REPO_ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
    # worker 段（至下一个服务定义前）必须显式出现 engine_mode 覆盖
    worker_section = re.search(
        r"^  worker:.*?(?=^  \w|\Z)", compose, flags=re.MULTILINE | re.DOTALL
    )
    assert worker_section, "compose 缺少 worker 服务定义"
    assert "ETL_AGENT_ENGINE_MODE: mock" in worker_section.group(0), (
        "worker 必须显式声明 ETL_AGENT_ENGINE_MODE（F5：演示口径" 
        "可审、可关；缺省 real 由 config.py 保证）"
    )
