#!/usr/bin/env python3
"""模型数据保鲜检查（对齐 TS ``scripts/check-model-data.ts``）。

离线校验生成的数据分片与 manifest 的一致性——不访问网络。
漂移/缺失时 exit 1 并提示重新生成；pytest 保鲜测试也走同一条路径。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 产物目标：nova_ai 包根（默认 monorepo 兄弟目录约定，NOVA_AI_PACKAGE_ROOT 可覆盖）
NOVA_AI_ROOT = Path(
    os.environ.get("NOVA_AI_PACKAGE_ROOT")
    or str(Path(__file__).resolve().parents[2] / "nova_ai")
)

from nova_catalog import model_data as mdata  # noqa: E402


def main() -> int:
    try:
        mdata.validate_generated_model_data(NOVA_AI_ROOT)
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
