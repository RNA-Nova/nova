"""
nova_harness.modes.rpc.__main__ 单元测试。
"""

import importlib
import sys
from unittest.mock import patch


def test_main_module_invokes_main():
    """python -m nova_harness.rpc 应调用 cli.main()。"""
    with patch.object(sys, "argv", ["nova-harness-rpc"]):
        with patch("nova_harness.modes.rpc.cli.main") as mock_main:
            # 确保每次导入都重新执行模块级 main() 调用
            if "nova_harness.modes.rpc.__main__" in sys.modules:
                importlib.reload(sys.modules["nova_harness.modes.rpc.__main__"])
            else:
                importlib.import_module("nova_harness.modes.rpc.__main__")
            mock_main.assert_called_once()
