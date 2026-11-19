"""nova_gateway_protocol 的依赖方向结构核对。"""

import ast
from pathlib import Path


def test_protocol_only_depends_on_shared_protocol_and_itself():
    production_root = (
        Path(__file__).resolve().parents[1] / "src" / "nova_gateway_protocol"
    )
    allowed = frozenset({"nova_gateway_protocol", "nova_protocol"})
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
                root = module.split(".", 1)[0]
                if root.startswith("nova_") and root not in allowed:
                    offenders.append(
                        f"{path.name}:{getattr(node, 'lineno', 0)} imports {module}"
                    )
    assert offenders == []
