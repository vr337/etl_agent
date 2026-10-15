"""探针：确认 API-13 回滚路由已注册（DoD-1 / 验收命令）。

路由以子应用挂载，`app.routes` 只暴露 Mount 包装；故以 OpenAPI schema（权威来源）
核对 5 条 API-13 路径。注意 S3 另有一条凭据轮换 `/rollback` 路径，需按前缀区分。
"""
from __future__ import annotations

from fastapi import FastAPI


def test_rollback_routes_registered(app: FastAPI) -> None:
    paths = set(app.openapi()["paths"])
    api13 = {
        "/api/v1/projects/{project_id}/executions/{run_id}/rollbacks",
        "/api/v1/rollbacks/{rollback_id}",
        "/api/v1/rollbacks/{rollback_id}/decisions",
        "/api/v1/rollbacks/{rollback_id}/cleanups",
        "/api/v1/projects/{project_id}/rollbacks/cleanup-scan",
    }
    assert api13 <= paths, api13 - paths
