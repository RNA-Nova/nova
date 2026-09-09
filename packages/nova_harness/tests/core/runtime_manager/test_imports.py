"""导入无环不变量守门（冷启动子进程）+ sdk 层消灭守卫。

runtime_manager 包重组后的关键约束：``manager`` 模块级不拉起 assembly，
组装链不反向 import core/sdk，harness 层不存在 ``sdk`` 模块（对位
codex——进程内消费走 RuntimeManager，协议嵌入走 nova_server 客户端）。
任何一条被破坏，本组测试即失败。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys

import pytest

_MODULES = [
    "nova_harness",
    "nova_harness.core",
    "nova_harness.core.runtime_manager",
    "nova_harness.core.runtime_manager.assembly",
    "nova_harness.core.runtime_manager.factory",
]


@pytest.mark.parametrize("module", _MODULES)
def test_cold_import_succeeds(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_sdk_module_stays_gone() -> None:
    """sdk 模块已从 harness 层消灭——回归（重建 sdk.py）立即失败。"""
    assert importlib.util.find_spec("nova_harness.sdk") is None
    assert importlib.util.find_spec("nova_harness.core.sdk") is None


def test_manager_module_does_not_import_assembly_at_top_level() -> None:
    """manager 模块级不得 import assembly（懒加载不变量）。"""
    import nova_harness.core.runtime_manager.manager as manager

    source = open(manager.__file__, encoding="utf-8").read()
    for line in source.split("\n"):
        stripped = line.lstrip()
        if line[:1].isspace():
            continue  # 函数体内缩进 import = 懒加载，允许
        if stripped.startswith(("from ", "import ")):
            assert "assembly" not in stripped, stripped
            assert "nova_harness.sdk" not in stripped, stripped
