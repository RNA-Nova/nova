"""Command-line entry point for nova-harness-rpc."""

import argparse
import asyncio
import signal
import sys

from nova_harness.modes.rpc.output_guard import OutputGuard
from nova_harness.modes.rpc.server import NovaRpcServer


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nova-harness-rpc",
        description="Nova Harness JSON-RPC server over stdio",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )
    return parser


async def _async_main() -> int:
    _build_parser().parse_args()

    with OutputGuard() as guard:
        server = NovaRpcServer(output_guard=guard)

        loop = asyncio.get_running_loop()

        def _on_signal(signum: int) -> None:
            server.shutdown()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _on_signal, sig)

        try:
            await server.run()
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
    return 0


def main() -> int:
    """Synchronous entry point for console scripts."""
    try:
        return asyncio.run(_async_main())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
