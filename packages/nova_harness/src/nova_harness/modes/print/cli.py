"""Command-line entry point for print mode.

``nova-harness run`` 以 print 模式执行：非交互式地运行一次 agent 任务。
"""

from argparse import Namespace

from nova_harness.modes.print.runner import run_print_mode


def cmd_run(args: Namespace) -> int:
    """``nova-harness run`` 子命令入口。"""
    if not args.agent or not args.task:
        __import__("sys").stderr.write("error: agent and --task are required\n")
        return 2
    return __import__("asyncio").run(
        run_print_mode(
            agent_name=args.agent,
            task=args.task,
            cwd=args.cwd,
            json_output=args.json,
            no_session=args.no_session,
            trust=args.trust if args.trust else None,
        )
    )
