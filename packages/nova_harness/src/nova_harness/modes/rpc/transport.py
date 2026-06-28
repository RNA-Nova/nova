"""JSON-RPC over stdio transport layer."""

import asyncio
import json
import sys
from typing import Any, Dict, Optional


class StdioTransport:
    """Read NDJSON from stdin and write NDJSON to stdout."""

    def __init__(self) -> None:
        self._reader: Optional[asyncio.StreamReader] = None

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
            sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception:
            pass
