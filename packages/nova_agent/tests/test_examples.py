"""离线示例的编译与真实运行保鲜门禁。"""

import subprocess
import sys
from pathlib import Path

import pytest

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_EXAMPLES = sorted((_PACKAGE_ROOT / "examples").glob("*.py"))


def test_example_inventory_is_complete():
    assert [path.name for path in _EXAMPLES] == [
        "01_quickstart.py",
        "02_custom_tools.py",
        "03_hooks.py",
        "04_steering_followup.py",
        "05_abort_continue.py",
    ]


@pytest.mark.parametrize("path", _EXAMPLES, ids=lambda path: path.name)
def test_example_compiles(path):
    compile(path.read_text("utf-8"), str(path), "exec")


@pytest.mark.parametrize("path", _EXAMPLES, ids=lambda path: path.name)
def test_example_runs_offline(path):
    result = subprocess.run(
        [sys.executable, str(path)],
        cwd=_PACKAGE_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
