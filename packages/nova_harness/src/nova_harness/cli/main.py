"""Nova Harness CLI main entry.

This module provides the top-level ``nova-harness`` command.
Subcommands are implemented under ``nova_harness.cli``.
"""

import argparse
import sys

from nova_harness.core.utils.version import harness_version


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

    args = parser.parse_args(argv)

    if args.command == "run":
        from nova_harness.modes.print.cli import cmd_run

        return cmd_run(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
