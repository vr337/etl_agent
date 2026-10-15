"""S8 Worker 任务用例共享夹具。

监督周期任务需要一条「准备单 + 运行」的最小事实链，直接复用监督域工厂
`tests.supervision.conftest::make_run`（与 `tests/rollback/conftest.py` 同法）。
"""
from __future__ import annotations

from tests.supervision.conftest import make_run  # noqa: F401  -- 复用夹具

__all__: list[str] = []
