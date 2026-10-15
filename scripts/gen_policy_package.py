#!/usr/bin/env python
"""生成默认 Harness 策略包（清单 JSON + Ed25519 分离签名）。（T-12.4）

SPEC 依据：etl_agent-SPEC_v1.0.3-Baseline 第 3.2 章 M-J / 第 8 章「安全 · 授权与职责分离」
覆盖 FR：FR-30（策略清单 rule_id/version 唯一 + 排序稳定 + SHA256 + Ed25519 分离签名）
对应 AC：AC-01 / AC-05（PDP 的可信策略来源）

用法（人类执行；私钥只来自环境变量，不落库、不进日志）：
    ETL_AGENT_HARNESS_SIGNING_KEY_HEX=<32字节hex> uv run python scripts/gen_policy_package.py

输出：
    configs/policy/harness-v2-20260808.json   清单（含 package_sha256）
    configs/policy/harness-v2-20260808.sig    Ed25519 分离签名（hex）
控制面启动时只加载并验签（`harness.policy.package.load_package`），不持有私钥。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from etl_agent.config import settings
from etl_agent.harness.policy.package import (
    DEFAULT_PACKAGE_VERSION,
    build_package,
    compute_package_sha256,
    sign_package,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析命令行参数（路径可覆盖，便于生成多版本策略包）。"""
    parser = argparse.ArgumentParser(description="生成 Harness 签名策略包")
    parser.add_argument(
        "--version",
        default=DEFAULT_PACKAGE_VERSION,
        help="策略包版本标识（默认 harness-v2-20260808）",
    )
    parser.add_argument("--out-json", default=settings.policy_package_path)
    parser.add_argument("--out-sig", default=settings.policy_package_signature_path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """生成策略包与签名；私钥缺失 / 非法直接失败退出（不产出半成品）。"""
    args = parse_args(argv)
    signing_key = settings.harness_signing_key_hex.strip()
    if not signing_key:
        print("错误：未配置 ETL_AGENT_HARNESS_SIGNING_KEY_HEX（32 字节 hex）", file=sys.stderr)
        return 2

    package = build_package(version=args.version)
    signature = sign_package(package, signing_key)

    # 生成后自校验：清单摘要必须可复现（防止产出不可验签的包）
    manifest_without_digest = {
        key: value for key, value in package.items() if key != "package_sha256"
    }
    if compute_package_sha256(manifest_without_digest) != package["package_sha256"]:
        print("错误：清单摘要自校验失败", file=sys.stderr)
        return 3

    json_path = Path(args.out_json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(package, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    sig_path = Path(args.out_sig)
    sig_path.parent.mkdir(parents=True, exist_ok=True)
    sig_path.write_text(signature + "\n", encoding="utf-8")

    print(f"策略包已生成：{json_path}")
    print(f"签名文件已生成：{sig_path}")
    print(f"package_sha256 = {package['package_sha256']}")
    print(f"规则数 = {len(package['rules'])}，主机白名单 = {len(package['host_whitelist'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
