"""nova_catalog 的依赖方向结构核对。"""

import ast
from pathlib import Path

_ALLOWED_NOVA_PACKAGES = frozenset({"nova_catalog", "nova_protocol"})


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
    production_root = package_root / "src" / "nova_catalog"
    offenders = [
        offender
        for path in sorted(production_root.rglob("*.py"))
        for offender in _disallowed_nova_imports(path)
    ]
    assert offenders == []


def test_catalog_has_no_downstream_consumer_references():
    """下游包名也不得藏在路径、模板或其他字符串常量里。"""
    production_root = Path(__file__).resolve().parents[1] / "src" / "nova_catalog"
    offenders = [
        f"{path.relative_to(production_root)} mentions {consumer}"
        for path in sorted(production_root.rglob("*.py"))
        for consumer in ("nova_ai", "nova_gateway")
        if consumer in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
