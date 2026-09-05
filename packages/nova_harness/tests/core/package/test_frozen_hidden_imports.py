"""冻结形态 hidden-import 漂移测试。

风险类：仅被"运行时动态加载的包代码"（bundle 的工具/扩展/用户工具及其
包模块）引用的 stdlib 纯 Python 模块，对 PyInstaller 静态分析不可达——
不进二进制，表现为对应工具静默缺席（difflib 缺 → edit 工具消失的实录）。

本测试按 AST 差集复算风险集，核对 scripts/build-backend.sh 的
HIDDEN_IMPORTS 列表覆盖之：

    风险集 = bundle 动态面 stdlib import − 四个 --collect-all 包的静态面
             − sys.builtin_module_names（C 内建不需要 PYZ）

注：静态面只算了四个包自己的直接 import（第三方依赖的传递可达覆盖不到，
如 json 实际由 pydantic 带进）——因此差集是**高估**方向，宁可误报补登，
不放漏网。误报代价 = 多一条无害的 --hidden-import。
"""

from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]

# 运行时动态加载面（PyInstaller 分析不可达）
DYNAMIC_SURFACE = [REPO_ROOT / "bundles" / "nova_coding_agent" / "backend"]

# --collect-all 静态面（分析可达）
STATIC_SURFACE = [
    REPO_ROOT / "packages" / "nova_ai" / "src",
    REPO_ROOT / "packages" / "nova_agent" / "src",
    REPO_ROOT / "packages" / "nova_harness" / "src",
    REPO_ROOT / "bundles" / "nova_base" / "backend",
]

BUILD_SCRIPT = REPO_ROOT / "scripts" / "build-backend.sh"


def _stdlib_imports(root: Path) -> set[str]:
    found: set[str] = set()
    for path in root.rglob("*.py"):
        if "__pycache__" in str(path) or f"{os.sep}tests{os.sep}" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.add(node.module.split(".")[0])
    return {name for name in found if name in sys.stdlib_module_names}


def test_hidden_imports_cover_dynamic_surface() -> None:
    if not BUILD_SCRIPT.exists():
        return  # 发布工程文件缺席时本测试无意义（如单独 checkout 子包）

    dyn: set[str] = set()
    for root in DYNAMIC_SURFACE:
        dyn |= _stdlib_imports(root)
    static: set[str] = set()
    for root in STATIC_SURFACE:
        static |= _stdlib_imports(root)

    risk = (dyn - static) - set(sys.builtin_module_names)

    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"HIDDEN_IMPORTS=\(([^)]*)\)", text)
    assert m, "build-backend.sh 缺少 HIDDEN_IMPORTS 列表"
    declared = set(m.group(1).split())

    missing = risk - declared
    assert not missing, (
        f"冻结缺席风险：{sorted(missing)} 仅被动态加载面包代码引用，"
        "须加入 build-backend.sh 的 HIDDEN_IMPORTS"
    )
