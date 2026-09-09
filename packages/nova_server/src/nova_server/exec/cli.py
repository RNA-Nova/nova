"""Command-line entry point for exec mode.

``nova-server exec`` 以 exec 模式执行：非交互式地运行一次 agent 任务。
"""

from argparse import Namespace

from nova_server.exec.runner import run_exec_mode


def cmd_run(args: Namespace) -> int:
    """``nova-server exec`` 子命令入口。"""
    if not args.agent or not args.task:
        __import__("sys").stderr.write("error: agent and --task are required\n")
        return 2

    def _split_csv(value: "str | None") -> "list[str] | None":
        if value is None:
            return None
        return [name.strip() for name in value.split(",") if name.strip()]

    return __import__("asyncio").run(
        run_exec_mode(
            agent_name=args.agent,
            task=args.task,
            cwd=args.cwd,
            json_output=args.json,
            no_session=args.no_session,
            trust=args.trust if args.trust else None,
            additional_skill_paths=args.skill,
            additional_prompt_template_paths=args.prompt_template,
            tools=_split_csv(args.tools),
            exclude_tools=_split_csv(args.exclude_tools),
        )
    )
