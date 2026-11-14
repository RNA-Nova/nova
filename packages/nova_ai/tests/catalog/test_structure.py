"""nova_ai.catalog 的依赖方向结构核对。"""

import ast
from pathlib import Path

_ALLOWED_NOVA_PACKAGES = frozenset({"nova_ai.catalog", "nova_protocol"})


def _disallowed_nova_imports(path: Path) -> list[str]:
    """目录叶子只允许 protocol 与自身；相对 import 天然不参与本检查。"""
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules = [node.module]
        for module in modules:
            root = module.split(".", 1)[0]
            if root.startswith("nova_") and root not in _ALLOWED_NOVA_PACKAGES:
                offenders.append(
                    f"{path.name}:{getattr(node, 'lineno', 0)} imports {module}"
                )
    return offenders


def test_catalog_only_depends_on_protocol_and_itself():
    package_root = Path(__file__).resolve().parents[1]
    production_roots = [
        package_root / "src" / "nova_ai.catalog",
        package_root / "scripts",
    ]
    offenders = [
        offender
        for root in production_roots
        for path in sorted(root.rglob("*.py"))
        for offender in _disallowed_nova_imports(path)
    ]
    assert offenders == []
