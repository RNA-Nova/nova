"""枢纽纯洁性审计——nova_protocol 三纪律的机械执法。

Python 没有编译期 crate 闸，本测试就是那道闸：

1. **零兄弟包依赖**：包内任何模块不得 import nova_ai / nova_agent /
   nova_harness / nova_server / nova_coding_agent / nova_executor*；
2. **零行为零 I/O**：包内任何模块不得 import 行为/IO 模块
   （asyncio / subprocess / socket / urllib / httpx / requests / os / sys /
   io / logging / pathlib）；
3. **依赖声明最小**：pyproject 的运行时依赖只允许 python + pydantic。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent / "src" / "nova_protocol"
PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

SIBLING_BAN = re.compile(r"^nova_(ai|agent|harness|server|coding_agent|executor)(\.|$)")

BEHAVIOR_BAN = {
    "asyncio",
    "subprocess",
    "socket",
    "urllib",
    "httpx",
    "requests",
    "os",
    "sys",
    "io",
    "logging",
    "pathlib",
}

# 白名单：signal.py 是全仓签名通用的取消原语（例外清单唯一成员，
# 见包 README）——其实现需要 asyncio，除此之外任何模块不得破例。
IMPORT_WHITELIST = {
    "signal.py": {"asyncio"},
}


def _iter_modules():
    return sorted(PKG_ROOT.rglob("*.py"))


def _imports_of(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                yield node.module


def test_no_sibling_imports():
    """纪律 1：任何模块不得 import 兄弟包。"""
    offenders = []
    for path in _iter_modules():
        for mod in _imports_of(path):
            if SIBLING_BAN.match(mod):
                offenders.append(f"{path.relative_to(PKG_ROOT)}: {mod}")
    assert not offenders, "枢纽包不得依赖兄弟包：\n" + "\n".join(offenders)


def test_no_behavior_or_io_imports():
    """纪律 2：任何模块不得 import 行为/IO 模块（白名单除外）。"""
    offenders = []
    for path in _iter_modules():
        allowed = IMPORT_WHITELIST.get(path.name, set())
        for mod in _imports_of(path):
            head = mod.split(".", 1)[0]
            if head in BEHAVIOR_BAN and head not in allowed:
                offenders.append(f"{path.relative_to(PKG_ROOT)}: {mod}")
    assert not offenders, "枢纽包只放纯形状（禁行为/IO）：\n" + "\n".join(offenders)


def test_pyproject_dependencies_minimal():
    """纪律 3：运行时依赖只允许 python + pydantic。"""
    text = PYPROJECT.read_text(encoding="utf-8")
    m = re.search(r"\[tool\.poetry\.dependencies\](.*?)(\n\[|\Z)", text, re.DOTALL)
    assert m, "pyproject 缺 [tool.poetry.dependencies] 段"
    section = m.group(1)
    keys = re.findall(r"^([A-Za-z0-9_-]+)\s*=", section, re.MULTILINE)
    assert set(keys) <= {
        "python",
        "pydantic",
    }, f"枢纽包运行时依赖越界：{sorted(set(keys))}"
