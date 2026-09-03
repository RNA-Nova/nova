#!/usr/bin/env python3
"""模型数据保鲜检查（对齐 TS ``scripts/check-model-data.ts``）。

离线校验生成的数据分片与 manifest 的一致性——不访问网络。
漂移/缺失时 exit 1 并提示重新生成；pytest 保鲜测试也走同一条路径。
"""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE_ROOT / "scripts"))

import model_data as mdata  # noqa: E402


def main() -> int:
    try:
        mdata.validate_generated_model_data(PACKAGE_ROOT)
    except Exception as error:
        print(error, file=sys.stderr)
        print(
            "\nModel data is missing or stale. "
            "Run `pixi run -e dev generate-models` from the repository root.",
            file=sys.stderr,
        )
        return 1
    print("Generated model data is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
