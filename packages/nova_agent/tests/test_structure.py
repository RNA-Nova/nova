"""nova_agent 的依赖方向与包级族文件住所审计。"""

import ast
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_ROOT = _PACKAGE_ROOT / "src" / "nova_agent"
_ALLOWED_NOVA_IMPORTS = frozenset(
    {"nova_agent", "nova_ai", "nova_async", "nova_protocol"}
)
_REGISTERED_FAMILY_MODULES = frozenset({"utils.py"})


def _nova_imports(path: Path):
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules = [node.module]
        for module in modules:
            root = module.split(".", 1)[0]
            if root.startswith("nova_"):
                yield getattr(node, "lineno", 0), root, module


def test_agent_only_depends_on_lower_runtime_layers():
    offenders = [
        f"{path.relative_to(_SOURCE_ROOT)}:{line} imports {module}"
        for path in sorted(_SOURCE_ROOT.rglob("*.py"))
        for line, root, module in _nova_imports(path)
        if root not in _ALLOWED_NOVA_IMPORTS
    ]
    assert offenders == []


def test_shared_family_modules_stay_registered_and_documented():
    candidates = [_SOURCE_ROOT / "utils.py"]
    shared_dir = _SOURCE_ROOT / "_shared"
    if shared_dir.exists():
        candidates.extend(shared_dir.glob("*.py"))

    assert {path.name for path in candidates} == _REGISTERED_FAMILY_MODULES
    for path in candidates:
        docstring = ast.get_docstring(ast.parse(path.read_text("utf-8")))
        assert docstring and docstring.splitlines()[0].strip(), path
