"""cli/backend.py 多模式分发器测试（打包形态统一入口）。"""

import sys
from unittest.mock import patch

from nova_harness.cli.backend import main


def test_dispatch_default_is_rpc():
    """裸跑（无子命令）进 rpc 模式。"""
    with patch("nova_harness.modes.rpc.cli.main", return_value=0) as rpc_main:
        with patch.object(sys, "argv", ["nova-server"]):
            assert main() == 0
        rpc_main.assert_called_once_with()


def test_dispatch_rpc_explicit():
    with patch("nova_harness.modes.rpc.cli.main", return_value=0) as rpc_main:
        with patch.object(sys, "argv", ["nova-server", "rpc", "--version"]):
            assert main() == 0
            # rpc CLI 从 sys.argv 取参——分发器须把余参写回（with 内断言，
            # patch 退出即恢复原 argv）
            assert sys.argv == ["nova-server", "--version"]
        rpc_main.assert_called_once_with()


def test_dispatch_run_forwards_run_subcommand():
    """run 模式把余参拼成 nova-harness 的 run 子命令形态。"""
    with patch("nova_harness.cli.main.main", return_value=0) as run_main:
        with patch.object(sys, "argv", ["nova-server", "run", "coding_agent", "--task", "hi"]):
            assert main() == 0
        run_main.assert_called_once_with(["run", "coding_agent", "--task", "hi"])


def test_dispatch_pkg_forwards_rest():
    with patch("nova_harness.cli.package.main", return_value=0) as pkg_main:
        with patch.object(sys, "argv", ["nova-server", "pkg", "list", "--json"]):
            assert main() == 0
        pkg_main.assert_called_once_with(["list", "--json"])


def test_dispatch_unknown_mode(capsys):
    with patch.object(sys, "argv", ["nova-server", "bogus"]):
        assert main() == 2
    assert "未知模式" in capsys.readouterr().err
