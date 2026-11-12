"""枢纽纯洁性审计——nova_protocol 三纪律的机械执法。

Python 没有编译期 crate 闸，本测试就是那道闸：

1. **零兄弟包依赖**：包内任何模块不得 import nova_ai / nova_agent /
   nova_harness / nova_server / nova_coding_agent / nova-exec-server*；
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

SIBLING_BAN = re.compile(
    r"^nova_(ai|agent|async|catalog|harness|server|coding_agent|executor|exec_server_client)(\.|$)"
)

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

# 白名单（登记制基础原语，见包 README 与 docs/crosscutting/structure-modeling.md
# §二第 1 层——两处同步）：
# - signal.py：取消原语，实现需要 asyncio。
# （task_tracker.py 已迁出归 nova_async——取消/等待机制族叶子包。）
IMPORT_WHITELIST = {
    "signal.py": {"asyncio"},
}

# 纯函数算法件不需要 import 例外；在此登记住所，I/O 仍由全局扫描统一禁止。
PURE_ALGORITHM_PATHS = {
    "exec_server_intent.py",
    "exec_server_policy",
    "exec_server_permissions",
    "sanitize.py",
}


def test_registered_pure_algorithm_paths_exist():
    missing = sorted(
        path for path in PURE_ALGORITHM_PATHS if not (PKG_ROOT / path).exists()
    )
    assert not missing, f"纯算法登记册指向不存在的住所：{missing}"


def test_public_exec_wire_types_have_docstrings():
    """公共线上类型必须说明契约用途，不再积累无名形状。"""
    tree = ast.parse((PKG_ROOT / "exec_server_wire.py").read_text(encoding="utf-8"))
    missing = [
        f"{node.lineno}:{node.name}"
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and not node.name.startswith("_")
        and ast.get_docstring(node) is None
    ]
    assert not missing, f"公共 exec 类型缺 docstring：{missing}"


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


def test_root_reexports_are_complete():
    """显式 import 的公共词汇必须全部进入 __all__，根级公共定义必须挂到门面。"""
    import nova_protocol

    tree = ast.parse((PKG_ROOT / "__init__.py").read_text(encoding="utf-8"))
    imported = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if (alias.asname or alias.name) != "*"
        and not (alias.asname or alias.name).startswith("_")
    }
    missing_from_all = sorted(imported - set(nova_protocol.__all__))
    assert not missing_from_all, f"根门面 __all__ 漏项：{missing_from_all}"

    missing_from_root = []
    for path in PKG_ROOT.glob("*.py"):
        if path.name == "__init__.py":
            continue
        module = ast.parse(path.read_text(encoding="utf-8"))
        for node in module.body:
            if isinstance(
                node, (ast.ClassDef, ast.FunctionDef)
            ) and not node.name.startswith("_"):
                if not hasattr(nova_protocol, node.name):
                    missing_from_root.append(f"{path.name}:{node.name}")
    assert not missing_from_root, f"根门面漏挂公共定义：{missing_from_root}"

    # 子包自行声明的公共词汇也必须递归挂到根门面，不能只查根级 .py。
    import importlib

    for module_name in (
        "nova_protocol.exec_server_permissions",
        "nova_protocol.exec_server_policy",
        "nova_protocol.agent_events",
    ):
        module = importlib.import_module(module_name)
        for name in module.__all__:
            assert hasattr(nova_protocol, name), f"根门面缺少 {module_name}.{name}"
            assert name in nova_protocol.__all__, f"__all__ 缺少 {module_name}.{name}"
