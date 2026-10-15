"""etl_agent 控制面 API 启动入口（Windows 兼容）。

SPEC 依据：SPEC v1.0.3-Baseline 第 4.3 章（进程与入口）
覆盖 FR：FR-59（健康检查）
对应 AC：AC-11（认证边界）

背景：psycopg（v3 异步）不支持 ProactorEventLoop；uvicorn 在 Windows 默认
用 Proactor。本入口显式创建 SelectorEventLoop，绕过 uvicorn 的默认策略。
"""
import asyncio
import selectors
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn


def main() -> None:
    config = uvicorn.Config(
        "etl_agent.main:app",
        host="0.0.0.0",
        port=8000,
        loop="none",          # 由我们自己提供 event loop
        reload=False,
    )
    server = uvicorn.Server(config)

    if sys.platform == "win32":
        # 显式创建 SelectorEventLoop（psycopg 需要）
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.serve())
        except KeyboardInterrupt:
            pass
        finally:
            loop.close()
    else:
        server.run()


if __name__ == "__main__":
    main()