"""M-H 不可变版本与设计审批 — 版本 Diff（Schema / HOCON / 质量规则）。

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-H（版本 Diff）/ 第 5.2 章 pipeline_artifacts
覆盖 FR：FR-27（不同版本的 Schema、HOCON 配置与质量规则差异对比）
对应 AC：AC-17（版本 Diff 视图的后端能力）

实现口径（S5 吸收 2 决议）：业务库只有制品**摘要**，正文在 `etl_checkpoint`。
因此本模块分两层：
- `diff_artifact_digests`：仅凭业务库即可给出的摘要级差异（恒可用）；
- `diff_plan` / `diff_hocon` / `diff_quality_contract`：**纯函数**，入参为调用方取回的正文
  （由 `generation.service.load_candidate_content` 从 Checkpoint 读出）。
纯函数不碰 IO，便于单测与复用（S6 禁止事项 21：不复述代码行为）。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

MISSING = "<缺失>"


def _columns_of(plan: Mapping[str, Any]) -> dict[str, str]:
    """取 plan 的「目标列 → 类型」映射（版本 Diff 的 Schema 维度）。"""
    sink = plan.get("sink")
    target_schema = (sink or {}).get("target_schema") if isinstance(sink, Mapping) else None
    columns = target_schema.get("columns") if isinstance(target_schema, Mapping) else None
    if not isinstance(columns, Sequence):
        return {}
    return {
        str(item.get("name")): str(item.get("type"))
        for item in columns
        if isinstance(item, Mapping)
    }


def diff_columns(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """列级差异：新增 / 删除 / 类型变更（列名与类型族均按字面比较，交由调用方解释）。"""
    old, new = _columns_of(before), _columns_of(after)
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    type_changed = [
        {"name": name, "before": old[name], "after": new[name]}
        for name in sorted(set(old) & set(new))
        if old[name] != new[name]
    ]
    return {"added": added, "removed": removed, "type_changed": type_changed}


def _rules_of(plan: Mapping[str, Any]) -> dict[str, Any]:
    """取质量契约的规则集合（predicates + routing），以 name / rule 为键。"""
    contract = plan.get("quality_contract")
    if not isinstance(contract, Mapping):
        return {}
    rules: dict[str, Any] = {}
    predicates = contract.get("predicates")
    if isinstance(predicates, Sequence):
        for item in predicates:
            if isinstance(item, Mapping) and item.get("name"):
                rules[f"predicate:{item['name']}"] = item.get("expression")
    routing = contract.get("routing")
    if isinstance(routing, Sequence):
        for item in routing:
            if isinstance(item, Mapping) and item.get("rule"):
                rules[f"routing:{item['rule']}"] = item.get("sink")
    return rules


def diff_quality_contract(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """质量规则差异：新增 / 删除 / 定义变更（含 sink 路由）。"""
    old, new = _rules_of(before), _rules_of(after)
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = [
        {"rule": key, "before": old[key], "after": new[key]}
        for key in sorted(set(old) & set(new))
        if old[key] != new[key]
    ]
    return {"added": added, "removed": removed, "changed": changed}


def diff_hocon(before: str | None, after: str | None) -> dict[str, Any]:
    """HOCON 行级差异：新增 / 删除行数与是否变化（不逐字回显正文，避免泄漏面扩大）。"""
    old = (before or "").splitlines()
    new = (after or "").splitlines()
    old_set, new_set = set(old), set(new)
    return {
        "changed": before != after,
        "added_lines": len(new_set - old_set),
        "removed_lines": len(old_set - new_set),
        "before_line_count": len(old),
        "after_line_count": len(new),
    }


def diff_artifact_digests(
    before: Mapping[str, str], after: Mapping[str, str]
) -> dict[str, dict[str, Any]]:
    """摘要级差异：按 `artifact_type` 比较两侧 SHA256（业务库即可给出，恒可用）。"""
    types = sorted(set(before) | set(after))
    return {
        artifact_type: {
            "before": before.get(artifact_type, MISSING),
            "after": after.get(artifact_type, MISSING),
            "changed": before.get(artifact_type) != after.get(artifact_type),
        }
        for artifact_type in types
    }


def diff_plans(
    before: Mapping[str, Any] | None, after: Mapping[str, Any] | None
) -> dict[str, Any]:
    """候选 plan 的完整差异（Schema + 质量契约 + 连接器）。"""
    old = before or {}
    new = after or {}
    return {
        "schema": diff_columns(old, new),
        "quality_contract": diff_quality_contract(old, new),
        "connectors": {
            role: {
                "before": (old.get(role) or {}).get("connector_type", MISSING),
                "after": (new.get(role) or {}).get("connector_type", MISSING),
            }
            for role in ("source", "sink")
        },
    }


def has_changes(diff: Mapping[str, Any]) -> bool:
    """差异是否非空（供前端「无变化」提示与测试断言）。"""
    if not diff:
        return False
    for value in diff.values():
        if isinstance(value, Mapping):
            if value.get("changed") is True:
                return True
            if has_changes(value):
                return True
        elif isinstance(value, Sequence) and not isinstance(value, str):
            if value:
                return True
        elif value not in (None, False, 0, ""):
            return True
    return False
