"""公共线上类型的文档完整性检查。"""

import ast
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1] / "src" / "nova_exec_protocol"


def test_public_wire_types_have_docstrings():
    tree = ast.parse((PKG_ROOT / "wire.py").read_text(encoding="utf-8"))
    missing = [
        f"{node.lineno}:{node.name}"
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and not node.name.startswith("_")
        and ast.get_docstring(node) is None
    ]
    assert not missing, f"公共 exec 类型缺 docstring：{missing}"
