"""JSON-RPC error definitions."""

from typing import Any


class JSONRPCError(Exception):
    """JSON-RPC protocol error."""

    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)
