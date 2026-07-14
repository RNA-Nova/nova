"""JSON-RPC over stdio transport layer."""

import asyncio
import json
import sys
from typing import Any, Dict, Optional

from nova_harness.modes.rpc.output_guard import OutputGuard


class StdioTransport:
    """Read NDJSON from stdin and write NDJSON to stdout."""

    def __init__(self, output_guard: Optional[OutputGuard] = None) -> None:
        self._reader: Optional[asyncio.StreamReader] = None
        self._output_guard = output_guard

    async def open(self) -> None:
        """Bind stdin to an async StreamReader."""
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        self._reader = reader

    async def readline(self) -> Optional[str]:
        """Read the next line from stdin. Returns None on EOF."""
        if self._reader is None:
            raise RuntimeError("Transport not opened")
        line = await self._reader.readline()
        if not line:
            return None
        return line.decode("utf-8").strip()

    def write(self, obj: Dict[str, Any]) -> None:
        """Write a JSON object as a single line to stdout."""
        try:
            guard = self._output_guard
            if guard is not None:
                with guard.protocol_write():
                    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
                    sys.stdout.flush()
            else:
                sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except Exception:
            pass
