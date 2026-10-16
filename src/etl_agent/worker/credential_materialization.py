"""M-K 执行 Worker — Secret 物化（占位符注入，AC-02 红线核心）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-K / FR-68（图内只传占位符，运行期
变量 Worker 侧物化）/ 第 8 章「安全 · 凭据保护」（模型侧永不可见连接密码）
覆盖 FR：FR-67/68（三层状态隔离：占位符进制品，真实值只在 Worker 运行期）
对应 AC：AC-02（凭据零明文：物化 HOCON 不落库 / 不进日志 / 不回响应）

契约（S6 吸收清单 5）：变量名必须与 `configs/credential_placeholders.yaml`
登记的占位符一致（SRC_HOST / SRC_PORT / SRC_DB / SRC_USER / SRC_PASSWORD /
SINK_HOST / SINK_PORT / SINK_DB / SINK_USER / SINK_PASSWORD / FILE_ASSET_BASE 共 11 个
登记项）——yaml 是唯一事实源：提示词 §8.3 写的 `SRC_DATABASE` 与 DoD 的「10 个」
均为笔误，实现按契约动态加载变量表（登记表增删不需改代码，见阶段报告偏差）。
取值来源：连接行（host/port/database_name 非密字段）+ SecretProvider.read(secret_ref)；
FILE_ASSET_BASE 取配置的挂载根目录。

红线（提示词禁止事项 14）：
- 物化后的 HOCON **不落库、不进日志、不回响应**，只在内存短暂持有；
- 占位符替换必须彻底——残留 `${` 即物化失败（宁可失败也不半物化提交引擎）；
- 真实值只从 SecretProvider 读，禁止从环境变量 / 配置直取口令（FR-67）。
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from etl_agent.execution.errors import CredentialMaterializationFailed

# 占位符契约文件（S6 登记，唯一事实来源）
PLACEHOLDER_CONTRACT_PATH = Path("configs/credential_placeholders.yaml")

# 占位符形态：${VAR}（HOCON 环境变量替换语法）
_PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")

# 非密端点字段 → 占位符前缀映射（连接行的非密字段直接可用）
_CONNECTION_FIELD_MAP: dict[str, str] = {
    "SRC_HOST": "host",
    "SRC_PORT": "port",
    "SRC_DB": "database_name",
    "SRC_USER": "username",
    "SINK_HOST": "host",
    "SINK_PORT": "port",
    "SINK_DB": "database_name",
    "SINK_USER": "username",
}


@dataclass(frozen=True)
class PlaceholderContract:
    """占位符契约（从 yaml 加载，Worker 物化的唯一变量名来源）。"""

    variables: tuple[str, ...]

    @property
    def secret_variables(self) -> tuple[str, ...]:
        """需要 SecretProvider 提供真实值的变量（口令类）。"""
        return tuple(name for name in self.variables if name.endswith("_PASSWORD"))


def load_placeholder_contract(
    path: Path | str = PLACEHOLDER_CONTRACT_PATH,
) -> PlaceholderContract:
    """加载占位符契约（S6 登记的 yaml；缺失即物化失败）。

    Raises:
        CredentialMaterializationFailed: 契约文件缺失 / 解析失败 / 无登记变量。
    """
    contract_path = Path(path)
    if not contract_path.exists():
        raise CredentialMaterializationFailed(
            f"占位符契约文件不存在：{contract_path}（FR-68 / S6 吸收 5）"
        )
    try:
        payload: Any = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CredentialMaterializationFailed("占位符契约文件不可解析") from exc
    entries = payload.get("placeholders") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or not entries:
        raise CredentialMaterializationFailed("占位符契约未登记任何变量（FR-68）")
    variables = tuple(
        str(item["variable"]) for item in entries if isinstance(item, dict) and item.get("variable")
    )
    if not variables:
        raise CredentialMaterializationFailed("占位符契约变量名为空（FR-68）")
    return PlaceholderContract(variables=variables)


def find_placeholders(hocon: str) -> set[str]:
    """HOCON 中出现的占位符变量名集合（物化前盘点）。"""
    return set(_PLACEHOLDER_PATTERN.findall(hocon))


def render_hocon(
    hocon: str,
    values: dict[str, str],
    contract: PlaceholderContract,
) -> str:
    """把 `${VAR}` 占位符替换为真实值（内存中短暂持有，不落库，AC-02）。

    Args:
        hocon: 含占位符的 HOCON 正文（版本制品 / Checkpoint 候选）。
        values: 变量名 → 真实值（口令来自 SecretProvider，非密字段来自连接行）。
        contract: 占位符契约（登记变量表）。

    Returns:
        str: 替换后的 HOCON（调用方负责在用完后释放引用）。

    Raises:
        CredentialMaterializationFailed: 契约外变量出现 / 有占位符无取值 / 替换后残留。
    """
    used = find_placeholders(hocon)
    unregistered = sorted(used - set(contract.variables))
    if unregistered:
        raise CredentialMaterializationFailed(
            f"HOCON 出现契约外占位符：{', '.join(unregistered)}（FR-68 变量名契约）"
        )
    missing = sorted(name for name in used if not values.get(name))
    if missing:
        raise CredentialMaterializationFailed(
            f"占位符缺少物化取值：{', '.join(missing)}（检查连接 Secret 与非密字段）"
        )

    def _replace(match: re.Match[str]) -> str:
        return values[match.group(1)]

    materialized = _PLACEHOLDER_PATTERN.sub(_replace, hocon)
    if find_placeholders(materialized):
        raise CredentialMaterializationFailed("物化后仍有占位符残留（半物化不得提交引擎）")
    return materialized


def clear_values(values: dict[str, str]) -> None:
    """显式清空物化取值（释放内存引用；AC-02：真实值不滞留）。"""
    values.clear()


def _apply_host_map(
    values: dict[str, str],
    prefix: str,
    host_map: Mapping[str, str] | None,
) -> None:
    """执行视角 host 重写（T-ENGINE-02）。

    worker 容器内 `127.0.0.1:13306` 这类宿主机回环地址不可达；连接行按宿主机视角
    填写，物化时按 `ETL_AGENT_HOST_MAP`（host:port → host:port）重写为容器可达名。

    语义：**只重写vals内对应端点的 HOST/PORT**；未命中直通；host 为 None 或
    host_map 为空 / None 时原样返回（null-safe，绝不抛错）。
    """
    if not host_map:
        return
    host = values.get(f"{prefix}_HOST")
    if not host:
        return
    port = values.get(f"{prefix}_PORT")
    key = f"{host}:{port}" if port else host
    replacement = host_map.get(key)
    if replacement is None:
        return
    parts = replacement.split(":", 1)
    values[f"{prefix}_HOST"] = parts[0]
    if len(parts) == 2:
        values[f"{prefix}_PORT"] = parts[1]


def values_for_connection(
    connection: Any,
    secret_values: dict[str, str],
    prefix: str,
    file_asset_base: str | None = None,
    host_map: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """按端点角色（SRC / SINK）组装物化取值（非密字段取连接行，口令取 SecretProvider）。

    Args:
        connection: DataConnection 行（host / port / database_name / secret_ref）。
        secret_values: SecretProvider.read 返回的明文（username / password 等）。
        prefix: SRC 或 SINK。
        file_asset_base: FILE_ASSET_BASE 取值（来源为文件资产时注入）。
        host_map: 执行视角 host 映射（T-ENGINE-02；None / 空 = 不重写）。仅本函数
            （worker 物化路径）消费——API 探测路径用连接行原值，两视角各取所需。

    Returns:
        dict[str, str]: 占位符变量名 → 真实值。
    """
    values: dict[str, str] = {}
    for variable, field in _CONNECTION_FIELD_MAP.items():
        if not variable.startswith(f"{prefix}_"):
            continue
        raw = getattr(connection, field, None)
        if raw is not None:
            values[variable] = str(raw)
    _apply_host_map(values, prefix, host_map)
    # 口令与非密用户名来自 SecretProvider（连接行不存口令，FR-04/06）
    username = secret_values.get("username")
    password = secret_values.get("password")
    if username:
        values[f"{prefix}_USER"] = username
    if password:
        values[f"{prefix}_PASSWORD"] = password
    if file_asset_base and prefix == "SRC":
        values["FILE_ASSET_BASE"] = file_asset_base
    return values
