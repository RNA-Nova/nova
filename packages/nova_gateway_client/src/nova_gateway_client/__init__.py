"""独立的 Nova Gateway 官方客户端 SDK。"""

from .catalog import CatalogResult
from .client import NovaGatewayClient
from .errors import (
    GatewayAuthenticationError,
    GatewayBadRequestError,
    GatewayConflictError,
    GatewayConnectionError,
    GatewayDisconnectedError,
    GatewayError,
    GatewayHttpError,
    GatewayInternalServerError,
    GatewayNotFoundError,
    GatewayPermissionDeniedError,
    GatewayProtocolError,
    GatewayRateLimitError,
    GatewayTimeoutError,
    GatewayUnprocessableEntityError,
)
from .response import GatewayResponse
from .streaming import GatewayEventStream
from .transport import JitterFn, ResponseObserver, SleepFn

__all__ = [
    "CatalogResult",
    "GatewayAuthenticationError",
    "GatewayBadRequestError",
    "GatewayConflictError",
    "GatewayConnectionError",
    "GatewayDisconnectedError",
    "GatewayError",
    "GatewayEventStream",
    "GatewayHttpError",
    "GatewayInternalServerError",
    "GatewayNotFoundError",
    "GatewayPermissionDeniedError",
    "GatewayProtocolError",
    "GatewayRateLimitError",
    "GatewayResponse",
    "GatewayTimeoutError",
    "GatewayUnprocessableEntityError",
    "JitterFn",
    "NovaGatewayClient",
    "ResponseObserver",
    "SleepFn",
]
