"""
nova_harness.main 单元测试。
"""

import argparse
import sys
from unittest.mock import patch

import pytest

from nova_harness.cli.main import _extract_extension_flags, _extract_run_extension_flags


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


def _run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("agent", nargs="?")
    parser.add_argument("--task")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-session", action="store_true")
    return parser


def test_extract_extension_flags_collects_undeclared_long_options():
    """未声明长选项收进 flag 表：裸旗标 True、=形收值。"""
    rest, flags = _extract_extension_flags(
        ["coding_agent", "--task", "x", "--plan", "--tag=nightly"], _run_parser()
    )
    assert flags == {"plan": True, "tag": "nightly"}
    assert rest == ["coding_agent", "--task", "x"]


def test_extract_extension_flags_bare_flag_never_eats_positional():
    """裸旗标不消费下一个 argv（位置参 agent 被吞即歧义）。"""
    rest, flags = _extract_extension_flags(["--plan", "coding_agent"], _run_parser())
    assert flags == {"plan": True}
    assert rest == ["coding_agent"]


def test_extract_extension_flags_known_and_double_dash_passthrough():
    """已声明选项原样保留；-- 之后停止收集。"""
    rest, flags = _extract_extension_flags(
        ["--no-session", "--", "--plan"], _run_parser()
    )
    assert flags == {}
    assert rest == ["--no-session", "--", "--plan"]


def test_extract_run_extension_flags_only_for_run():
    """非 run 子命令保持严格解析（不做宽松收集）。"""
    argv, flags = _extract_run_extension_flags(["pkg", "list"], _run_parser())
    assert argv == ["pkg", "list"]
    assert flags == {}

    argv, flags = _extract_run_extension_flags(
        ["run", "a", "--task", "x", "--plan"], _run_parser()
    )
    assert argv == ["run", "a", "--task", "x"]
    assert flags == {"plan": True}


def test_main_run_passes_extension_flags_to_cmd_run():
    """main() 接线：run 的扩展 flag 透传给 cmd_run。"""
    with patch("nova_harness.modes.print.cli.cmd_run", return_value=0) as mock_run:
        from nova_harness.cli.main import main

        assert main(["run", "coding_agent", "--task", "x", "--plan"]) == 0
    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    assert kwargs["extension_flags"] == {"plan": True}
