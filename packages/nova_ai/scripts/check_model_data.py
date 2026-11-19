#!/usr/bin/env python3
"""模型数据一致性检查：离线对账聚合器、shard、JSON 与 manifest。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from . import model_data as mdata


def main(*, package_root: Optional[Path] = None) -> int:
    root = package_root or mdata.default_package_root()
    try:
        mdata.validate_generated_model_data(root)
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
