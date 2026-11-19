"""下游 Bearer 鉴权、token 供给与公网启动门。"""

from __future__ import annotations

import hmac
import ipaddress
import json
import secrets
from pathlib import Path
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from nova_gateway_protocol import ErrorPayload, ErrorResponse
from nova_protocol import NovaErrorKind
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from .config import GatewayConfig


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def provision_token(
    explicit: Optional[str], token_file: Optional[str], default_file: Path
) -> tuple[str, Optional[Path]]:
    """显式 > 指定文件 > 默认文件（无则生成并落盘 0600）。"""
    if explicit:
        return explicit, None
    path = Path(token_file) if token_file else default_file
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            token = data.get("token")
            if isinstance(token, str) and token:
                return token, path
        except Exception:
            pass
    token = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": token}, indent=2), encoding="utf-8")
    path.chmod(0o600)
    return token, path


def ensure_bind_security(config: GatewayConfig) -> None:
    if not is_loopback(config.host) and not config.api_token:
        raise ValueError(
            f"Refusing to bind non-loopback {config.host} without an explicit token"
        )


class GatewayAuthMiddleware(BaseHTTPMiddleware):
    """除 health 与公共目录外，全部推理入口强制 Bearer。"""

    _PUBLIC_EXACT = frozenset({"/healthz", "/api/models/providers"})

    def __init__(self, app: ASGIApp, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in self._PUBLIC_EXACT or path.startswith("/api/models/providers/"):
            return await call_next(request)
        presented = request.headers.get("Authorization", "")
        token = presented[len("Bearer ") :] if presented.startswith("Bearer ") else ""
        if not hmac.compare_digest(token, self._token):
            payload = ErrorResponse(
                error=ErrorPayload(
                    message="Unauthorized", kind=NovaErrorKind.UNAUTHORIZED
                )
            )
            return JSONResponse(payload.dump_wire(), status_code=401)
        return await call_next(request)


__all__ = [
    "GatewayAuthMiddleware",
    "ensure_bind_security",
    "is_loopback",
    "provision_token",
]
