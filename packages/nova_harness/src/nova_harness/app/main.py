"""Nova Harness CLI main entry.

This module provides the top-level ``nova-harness`` command.
Headless 运行（exec）在 ``nova_server.exec``（协议客户端形态）。
"""

import argparse
import sys


def main(argv=None):
    """Main entry point for the ``nova-harness`` CLI."""
    parser = argparse.ArgumentParser(
        prog="nova-harness",
        description="Nova Harness — Agent runtime and utilities.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    args = parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
