"""
nova_harness.main 单元测试。
"""

import sys
from unittest.mock import patch

import pytest


def test_main_module_invokes_cli_main():
    """直接执行 nova_harness.main 应调用 cli.main() 并返回其退出码。"""
    with patch("nova_harness.cli.main", return_value=0) as mock_main:
        # 通过 runpy 重新加载模块以触发 if __name__ == "__main__" 分支
        import runpy

        with patch.object(sys, "argv", ["nova_harness"]):
            with pytest.raises(SystemExit) as exc_info:
                runpy.run_module("nova_harness.main", run_name="__main__")
            assert exc_info.value.code == 0
        mock_main.assert_called_once_with()
