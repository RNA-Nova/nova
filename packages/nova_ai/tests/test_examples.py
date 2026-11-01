"""示例代码保鲜（对位 pi "documentation example compiles and works"）。

examples/ 下的示例至少过编译级冒烟——文档永不腐的第一道闸；
真实调用需要 API key，归 integration，不在此列。
"""

import py_compile
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def test_examples_exist():
    examples = list(EXAMPLES_DIR.glob("*.py"))
    assert examples, "examples/ 目录下应有示例文件"


def test_examples_compile(tmp_path):
    for example in sorted(EXAMPLES_DIR.glob("*.py")):
        py_compile.compile(
            str(example),
            cfile=str(tmp_path / f"{example.stem}.pyc"),
            doraise=True,
        )
