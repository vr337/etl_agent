"""M-G 确定性门禁 — GATE-010 HDFS 文件契约。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-G / 第 3.2 章 M-C（HDFS 连接器）
覆盖 FR：FR-19（HDFS 文件契约）
对应 AC：AC-04（不合规候选被拦截）、AC-02（路径不得夹带凭据）

判定：来源 / 目标为 HDFS 时，必须声明绝对路径 + 受支持文件格式，且路径不得内嵌凭据
（`hdfs://user:password@host/...` 是典型的明文凭据走私路径）。
"""
from __future__ import annotations

import re

from etl_agent.gate.contracts import SEVERITY_ERROR, Context, Plan, RuleResult

SUPPORTED_HDFS_FORMATS = frozenset({"TEXT", "CSV", "JSON", "PARQUET", "ORC"})
# userinfo 形式（scheme://user:pass@host）在 URI 中携带明文凭据
CREDENTIAL_IN_URI = re.compile(r"://[^/@\s]+:[^/@\s]+@")


class HdfsContract:
    """GATE-010：HDFS 路径与格式契约。"""

    rule_id = "GATE-010"
    description = "HDFS 文件契约"
    severity = SEVERITY_ERROR

    def check(self, plan: Plan, hocon: str, context: Context) -> RuleResult:
        hdfs_roles = [
            role
            for role in ("source", "sink")
            if str((plan.get(role) or {}).get("connector_type") or "").upper() == "HDFS"
        ]
        if not hdfs_roles:
            return RuleResult.of(self, True, "无 HDFS 端点，无需 HDFS 契约")
        hdfs = plan.get("hdfs")
        if not isinstance(hdfs, dict):
            return RuleResult.of(self, False, "HDFS 端点缺少 hdfs 契约块")
        path = str(hdfs.get("path") or "")
        file_format = str(hdfs.get("format") or "").upper()
        if not path.startswith("/"):
            return RuleResult.of(self, False, "hdfs.path 必须是绝对路径")
        if CREDENTIAL_IN_URI.search(path):
            return RuleResult.of(self, False, "hdfs.path 内嵌明文凭据（URI userinfo）")
        if file_format not in SUPPORTED_HDFS_FORMATS:
            return RuleResult.of(
                self,
                False,
                f"不支持的 HDFS 格式：{file_format or '<缺失>'}（白名单 {', '.join(sorted(SUPPORTED_HDFS_FORMATS))}）",
            )
        return RuleResult.of(self, True, f"HDFS 契约齐备（格式 {file_format}）")
