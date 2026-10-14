"""控制面入口（S1 骨架）：FastAPI 应用与 /health（FR-59）。"""
from __future__ import annotations

import uvicorn
from fastapi import FastAPI

app = FastAPI(
    title="etl_agent",
    version="0.1.0",
    description="生产级 ETL-Agent 数据集成平台（控制面治理 + SeaTunnel 数据面搬运）",
)


@app.get("/health")
async def health() -> dict[str, str]:
    """存活状态（FR-59：脱敏配置状态在后续阶段补充）。"""
    return {"status": "ok"}


def main() -> None:
    """uvicorn 入口（pyproject scripts: etl-agent-api）。"""
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
