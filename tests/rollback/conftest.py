"""S8 回滚域共享夹具（复用监督域的最小可运行事实链工厂）。

`ROLLBACK_DORIS` / `CLEANUP_DORIS` 走 S6 Harness 协议所需的策略包与职责夹具
（`harness_package` / `harness_roles` 等）**只在 `test_side_effects.py` 内显式引入**——
它们是 autouse 的（会给 outsider 授予项目 A 身份），若在此处重新导出会污染
`test_router.py` 的「跨项目 403」用例。
"""

from __future__ import annotations

from tests.supervision.conftest import make_run  # noqa: F401  -- 复用夹具

__all__: list[str] = []
