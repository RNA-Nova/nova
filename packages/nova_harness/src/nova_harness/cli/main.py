"""Nova Harness CLI main entry.

This module provides the top-level ``nova-harness`` command.
Subcommands are implemented under ``nova_harness.cli``.
"""

import argparse
import sys
from typing import Any, Dict, List, Tuple

from nova_harness.core.utils.version import harness_version


def _extract_extension_flags(
    run_args: List[str], run_parser: argparse.ArgumentParser
) -> Tuple[List[str], Dict[str, Any]]:
    """把 run 参数里未声明的长选项收进扩展 flag 表，其余原样交还 argparse。

    扩展经 ``registerFlag`` 注册的命名开关在装配期才知道合法集，解析期
    只能宽松收集：``--name=value`` 收值，裸 ``--name`` 收 True（不消费
    下一个 argv——位置参（agent 名）被吞的歧义不收；string 类型 flag
    用 ``=`` 形）。``--`` 之后的内容原样保留（位置参语义）。
    """
    known_longs = {
        opt[2:]
        for action in run_parser._actions
        for opt in action.option_strings
        if opt.startswith("--")
    }
    rest: List[str] = []
    flags: Dict[str, Any] = {}
    passthrough = False
    for arg in run_args:
        if passthrough:
            rest.append(arg)
            continue
        if arg == "--":
            passthrough = True
            rest.append(arg)
            continue
        if arg.startswith("--"):
            name, eq, value = arg[2:].partition("=")
            if name and name not in known_longs:
                flags[name] = value if eq else True
                continue
        rest.append(arg)
    return rest, flags


def _extract_run_extension_flags(
    argv: Any, run_parser: argparse.ArgumentParser
) -> Tuple[Any, Dict[str, Any]]:
    """只在 run 子命令上启用扩展 flag 收集（其余子命令保持严格解析）。"""
    raw = list(argv) if argv is not None else sys.argv[1:]
    if raw[:1] != ["run"]:
        return argv, {}
    rest, flags = _extract_extension_flags(raw[1:], run_parser)
    return ["run", *rest], flags


def main(argv=None):
    """Main entry point for the ``nova-harness`` CLI."""
    parser = argparse.ArgumentParser(
        prog="nova-harness",
        description="Nova Harness — Agent runtime and utilities.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {harness_version()}",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # run
    p_run = sub.add_parser(
        "run",
        help="Run an installed agent with a single task",
    )
    p_run.add_argument("agent", nargs="?", help="Name of the installed agent to run")
    p_run.add_argument("--task", help="Task to delegate to the agent")
    p_run.add_argument("--cwd", help="Working directory for the agent")
    p_run.add_argument("--json", action="store_true", help="Output JSONL event stream")
    p_run.add_argument(
        "--trust",
        action="store_true",
        help="Trust the current project folder (load .nova settings and resources)",
    )
    p_run.add_argument(
        "--no-session",
        action="store_true",
        help="Do not persist the session to disk (ephemeral)",
    )
    p_run.add_argument(
        "--skill",
        action="append",
        default=[],
        metavar="PATH",
        help="Temporarily load a skill from a local path for this run "
        "(repeatable; not persisted)",
    )
    p_run.add_argument(
        "--prompt-template",
        action="append",
        default=[],
        metavar="PATH",
        help="Temporarily load a prompt template from a local path for this run "
        "(repeatable; not persisted)",
    )
    p_run.add_argument(
        "--tools",
        "-t",
        metavar="NAMES",
        help="Comma-separated allowlist of tool names to enable "
        "(SDK 层宿主硬闸的 CLI 投影)",
    )
    p_run.add_argument(
        "--exclude-tools",
        "-xt",
        metavar="NAMES",
        help="Comma-separated denylist of tool names to disable "
        "(applied after --tools)",
    )

    args, extension_flags = _extract_run_extension_flags(argv, p_run)
    args = parser.parse_args(args)

    if args.command == "run":
        from nova_harness.modes.print.cli import cmd_run

        return cmd_run(args, extension_flags=extension_flags or None)

    parser.print_help()
    return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
