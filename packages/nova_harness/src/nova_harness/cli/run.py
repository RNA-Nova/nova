"""Implementation of ``nova-harness run``.

Runs a single installed agent with a one-off task and streams the result
as JSONL events. By default the session is ephemeral (not persisted).
"""

import asyncio
import os
from argparse import Namespace

from nova_harness.core.config.defaults import get_agent_dir
from nova_harness.core.sdk import CreateAgentSessionOptions, create_agent_session


async def _run_agent(args: Namespace) -> int:
    agent_dir = get_agent_dir()
    cwd = args.cwd if args.cwd else os.getcwd()

    runtime = await create_agent_session(
        CreateAgentSessionOptions(
            cwd=cwd,
            agent_dir=agent_dir,
            agent_name=args.agent,
        )
    )

    try:
        await runtime.session.prompt(args.task)
        await runtime.session.agent.wait_for_idle()
    finally:
        await runtime.dispose()

    return 0


def cmd_run(args: Namespace) -> int:
    return asyncio.run(_run_agent(args))
