"""nova_logging 的独立叶子包边界核对。"""

import ast
from pathlib import Path


def test_logging_does_not_depend_on_nova_business_packages():
    production_root = Path(__file__).resolve().parents[1] / "src" / "nova_logging"
    offenders: list[str] = []
    for path in sorted(production_root.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                modules = [node.module]
            for module in modules:
                if module.split(".", 1)[0].startswith("nova_"):
                    offenders.append(
                        f"{path.name}:{getattr(node, 'lineno', 0)} imports {module}"
                    )
    assert offenders == []
