"""WebSocket 传输（连接化 P1）：``Transport`` 实现 + acceptor。

协议帧与 stdio 完全一致（JSON 文本帧，一行一帧的约束解除——WS 自带
帧边界）；只是把字节通道换成 WebSocket。

鉴权三守则（codex app-server 的本地版）：

- **Bearer token**：``Authorization: Bearer <token>`` 头（Node 客户端）
  或 ``?token=`` query（浏览器 WebSocket API 不能自定义头——web UI
  落地通道）；``hmac.compare_digest`` 常数时间比较；
- **非 loopback 监听且无显式 token → 拒绝启动**（构造期 ValueError）；
- **Origin 白名单**：带 ``Origin`` 头的请求不在白名单 → 403（防浏览器
  跨站 WS 劫持）。白名单缺省为空 = 一切带 Origin 的请求都拒；本地
  web/桌面端落地时按发布源配置。

token 供给：显式 ``--token`` > ``--token-file`` > 自动生成并落
``~/.nova/agent/rpc-server.json``（0600，含 url+token，客户端读它接入）。
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import secrets
import urllib.parse
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, Set

import websockets
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request

from nova_harness.server.transport.base import Transport

# on_connection 回调：每接入一条连接调用一次（服务器 add_connection 的接缝）
OnConnection = Callable[[Transport], Awaitable[None]]


class WebSocketTransport(Transport):
    """单条 WebSocket 连接的 Transport 适配（帧边界由 WS 协议保证）。"""

    def __init__(self, ws: ServerConnection) -> None:
        self._ws = ws

    @property
    def supports_binary(self) -> bool:
        return True

    async def open(self) -> None:
        """已由 acceptor 在 upgrade 时打开（空操作）。"""

    async def read(self) -> Dict[str, Any] | None:
        try:
            message = await self._ws.recv()
        except ConnectionClosed:
            return None
        if isinstance(message, bytes):
            # 二进制帧不承载 RPC 消息（协议为 JSON 文本帧）——跳过
            return await self.read()
        return json.loads(message)

    async def write(self, msg: Dict[str, Any]) -> None:
        await self._ws.send(json.dumps(msg, ensure_ascii=False))

    async def send_binary(
        self, data: bytes, metadata: Dict[str, Any] | None = None
    ) -> None:
        await self._ws.send(data)

    async def receive_binary(self) -> tuple[bytes, Dict[str, Any]] | None:
        try:
            message = await self._ws.recv()
        except ConnectionClosed:
            return None
        if isinstance(message, str):
            return await self.receive_binary()
        return message, {}

    async def close(self) -> None:
        try:
            await self._ws.close()
        except Exception:
            pass


def _is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def provision_token(
    explicit: Optional[str],
    token_file: Optional[str],
    default_file: Path,
) -> tuple[str, Optional[Path]]:
    """token 供给链：显式 > 指定文件 > 默认文件（无则生成落盘 0600）。

    返回 (token, 落盘路径)；显式 token 不落盘（返回 None 路径）。
    """
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


class WebSocketAcceptor:
    """WS 监听源：accept 一条接一条，经 ``on_connection`` 接入服务器。"""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        token: str,
        allow_origins: Optional[Set[str]] = None,
        on_connection: OnConnection,
    ) -> None:
        # 非 loopback 监听必须显式给了 token（自动落盘的本地 token 只配
        # loopback 场景）——裸网监听无鉴权直接拒启
        if not _is_loopback(host) and not token:
            raise ValueError(
                f"Refusing to bind non-loopback {host} without an explicit token"
            )
        self._host = host
        self._port = port
        self._token = token
        self._allow_origins = allow_origins or set()
        self._on_connection = on_connection
        self._server: Optional[Server] = None

    @property
    def port(self) -> int:
        """实际绑定端口（port=0 时有效）。"""
        if self._server is not None and self._server.sockets:
            return int(self._server.sockets[0].getsockname()[1])
        return self._port

    async def start(self) -> None:
        self._server = await serve(
            self._handler,
            self._host,
            self._port,
            process_request=self._process_request,
        )

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    # ------------------------------------------------------------------
    # upgrade 鉴权
    # ------------------------------------------------------------------

    def _process_request(self, connection: ServerConnection, request: Request) -> Any:
        headers = request.headers
        origin = headers.get("Origin")
        if origin is not None and origin not in self._allow_origins:
            return connection.respond(403, "Forbidden origin\n")

        # Authorization 头优先；?token= query 兜底（浏览器 WS 不能自定义头）
        auth = headers.get("Authorization", "")
        presented = ""
        if auth.startswith("Bearer "):
            presented = auth[len("Bearer ") :]
        else:
            query = urllib.parse.urlparse(request.path).query
            presented = urllib.parse.parse_qs(query).get("token", [""])[0]
        if not hmac.compare_digest(presented, self._token):
            return connection.respond(401, "Unauthorized\n")
        return None

    # ------------------------------------------------------------------
    # 连接接入
    # ------------------------------------------------------------------

    async def _handler(self, ws: ServerConnection) -> None:
        transport = WebSocketTransport(ws)
        await self._on_connection(transport)
        # handler 须挂起至连接关闭（否则库会主动收掉 ws）；关闭的发起
        # 在服务器侧（读泵 None → conn.close → transport.close）
        await ws.wait_closed()


__all__ = ["WebSocketAcceptor", "WebSocketTransport", "provision_token"]
