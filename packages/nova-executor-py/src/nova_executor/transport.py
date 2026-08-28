"""传输层：连接管理与 JSON-RPC 消息收发

- `WebSocketTransport`：ws:// / wss:// 远程连接
- `StdioTransport`：spawn 子进程，stdin/stdout 承载 NDJSON JSON-RPC
  （本地 `nova-executor --listen stdio` 或 SSH 远程同一实现）
- `Transport`：两种传输与连接池（pool.TransportPool）共同遵守的接口协议，
  上层（client/各管理器）只面向它编程
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import websockets
from websockets.client import WebSocketClientProtocol

from .errors import (
    AuthError,
    ConnectionError,
    ExecutorError,
    ProtocolError,
    TimeoutError,
)

logger = logging.getLogger(__name__)


class Transport(Protocol):
    """传输层接口协议（结构化类型——实现类无需显式继承）。

    `channel` 参数只在连接池（TransportPool）上有路由意义；单传输实现
    接收但忽略它，保持接口统一。
    """

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
    ) -> Any: ...

    async def send_notification(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
    ) -> None: ...

    def on_notification(self, handler: Callable[[dict], Awaitable[None]]) -> None: ...

    @property
    def is_connected(self) -> bool: ...


class WebSocketTransport:
    """WebSocket 传输层，负责连接管理和 JSON-RPC 消息收发"""

    def __init__(
        self,
        url: str,
        token: str | None = None,
        max_payload: int = 100 * 1024 * 1024,
        request_timeout: float = 30.0,
    ):
        self.url = url
        self.token = token
        self.max_payload = max_payload
        self.request_timeout = request_timeout
        self._ws: WebSocketClientProtocol | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._notification_handlers: list[Callable[[dict], Awaitable[None]]] = []
        self._receive_task: asyncio.Task | None = None
        self._closed = False

    async def connect(self) -> None:
        """建立 WebSocket 连接"""
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            self._ws = await websockets.connect(
                self.url,
                additional_headers=headers,
                max_size=self.max_payload,
            )
        except websockets.exceptions.InvalidStatusCode as e:
            if e.status_code == 401:
                raise AuthError(
                    "authentication failed: invalid or missing token"
                ) from e
            raise ConnectionError(f"failed to connect to {self.url}: {e}") from e
        except Exception as e:
            raise ConnectionError(f"failed to connect to {self.url}: {e}") from e

        self._closed = False
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def disconnect(self) -> None:
        """断开连接"""
        self._closed = True
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
            self._ws = None

        # 取消所有 pending 请求
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("connection closed"))
        self._pending.clear()

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
    ) -> Any:
        """发送 JSON-RPC 请求并等待响应（单传输忽略 channel——连接池路由参数）"""
        if not self._ws:
            raise ConnectionError("not connected")

        self._request_id += 1
        request_id = self._request_id

        message = {
            "id": request_id,
            "method": method,
            "params": params or {},
        }

        future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future

        try:
            await self._ws.send(json.dumps(message))
            return await asyncio.wait_for(future, timeout=self.request_timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise TimeoutError(f"request {method} timed out") from None
        except Exception as e:
            self._pending.pop(request_id, None)
            # 业务错误（JSON-RPC error 响应的 ProtocolError、连接已断）原样透传，
            # 只把传输层发送失败包装为 ConnectionError
            if isinstance(e, ExecutorError):
                raise
            raise ConnectionError(f"failed to send request {method}: {e}") from e

    async def send_notification(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
    ) -> None:
        """发送 JSON-RPC 通知（无响应；单传输忽略 channel——连接池路由参数）"""
        if not self._ws:
            raise ConnectionError("not connected")

        message = {
            "method": method,
            "params": params or {},
        }
        await self._ws.send(json.dumps(message))

    def on_notification(self, handler: Callable[[dict], Awaitable[None]]) -> None:
        """注册通知处理器"""
        self._notification_handlers.append(handler)

    async def _receive_loop(self) -> None:
        """接收消息循环"""
        try:
            async for raw in self._ws:
                if self._closed:
                    break
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if "id" in message and message["id"] is not None:
                    # 响应
                    request_id = message["id"]
                    future = self._pending.pop(request_id, None)
                    if future and not future.done():
                        if "error" in message:
                            error = message["error"]
                            future.set_exception(
                                ProtocolError(
                                    f"JSON-RPC error {error.get('code')}: {error.get('message')}"
                                )
                            )
                        else:
                            future.set_result(message.get("result"))
                elif "method" in message:
                    # 通知
                    for handler in self._notification_handlers:
                        try:
                            await handler(message)
                        except Exception:
                            pass
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._closed = True
            # 清理所有 pending
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("connection closed"))
            self._pending.clear()

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not self._closed


class StdioTransport:
    """stdio 传输层：spawn 子进程，stdin/stdout 承载 NDJSON JSON-RPC（一行一条消息）。

    command 参数化对齐 codex 的 `StdioExecServerCommand`（program + args + env + cwd），
    本地与 SSH 远程同一实现——调用方换 command 即可，不为 SSH 写专门类：

    - 本地：`StdioTransport()`（默认 `nova-executor --listen stdio`）
    - SSH：`StdioTransport(program="ssh", args=["user@host", "nova-executor", "--listen", "stdio"])`

    注意：stderr 由独立任务持续消费（打日志或回调），防止子进程 stderr
    缓冲填满死锁；进程退出传播为连接断开（pending 请求以 ConnectionError 收尾）。
    """

    #: 默认命令：本地 nova-executor 的 stdio 监听模式
    DEFAULT_PROGRAM = "nova-executor"
    DEFAULT_ARGS = ("--listen", "stdio")
    #: 单条 NDJSON 消息上限，与服务端 MAX_STDIO_JSONRPC_MESSAGE_LEN 对齐
    MAX_MESSAGE_SIZE = 64 * 1024 * 1024
    #: 关闭 stdin 后等待进程自行退出的宽限期（对齐服务端 2s 终止宽限）
    _SHUTDOWN_GRACE = 2.0

    def __init__(
        self,
        program: str = DEFAULT_PROGRAM,
        args: tuple[str, ...] | list[str] = DEFAULT_ARGS,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        request_timeout: float = 30.0,
        stderr_handler: Callable[[str], None] | None = None,
        max_message_size: int = MAX_MESSAGE_SIZE,
    ):
        self.program = program
        self.args = list(args)
        self.env = dict(env) if env else None
        self.cwd = cwd
        self.request_timeout = request_timeout
        self.stderr_handler = stderr_handler
        self.max_message_size = max_message_size
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._notification_handlers: list[Callable[[dict], Awaitable[None]]] = []
        self._receive_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._wait_task: asyncio.Task | None = None
        self._closed = True  # 未连接即关闭态

    async def connect(self) -> None:
        """spawn 子进程并启动收发循环"""
        # 对齐 Rust stdio_command_process：env 叠加在继承环境之上，而非整体替换
        env = {**os.environ, **self.env} if self.env else None
        try:
            self._process = await asyncio.create_subprocess_exec(
                self.program,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=env,
                # StreamReader 行缓冲上限：服务端单条消息可达 64MB
                limit=self.max_message_size,
            )
        except OSError as e:
            self._process = None
            raise ConnectionError(
                f"failed to spawn {self.program} {' '.join(self.args)}: {e}"
            ) from e

        self._closed = False
        self._receive_task = asyncio.create_task(self._receive_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())
        self._wait_task = asyncio.create_task(self._wait_loop())

    async def disconnect(self) -> None:
        """断开连接：关 stdin 让服务端自行退出，宽限后 terminate/kill 兜底"""
        self._closed = True
        for task in (self._receive_task, self._stderr_task, self._wait_task):
            if task:
                task.cancel()

        process = self._process
        if process is not None:
            # 先关 stdin：服务端读 EOF 即结束连接并自行退出（优雅路径）
            if process.stdin is not None and not process.stdin.is_closing():
                try:
                    process.stdin.close()
                except Exception:
                    pass
            await self._reap_process(process)
            self._process = None

        for task in (self._receive_task, self._stderr_task, self._wait_task):
            if task:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._receive_task = None
        self._stderr_task = None
        self._wait_task = None
        self._fail_pending()

    async def _reap_process(self, process: asyncio.subprocess.Process) -> None:
        """等待进程退出，逐级升级：EOF 宽限 → terminate → kill"""
        try:
            await asyncio.wait_for(process.wait(), timeout=self._SHUTDOWN_GRACE)
            return
        except asyncio.TimeoutError:
            pass
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=self._SHUTDOWN_GRACE)
            return
        except asyncio.TimeoutError:
            pass
        try:
            process.kill()
        except ProcessLookupError:
            return
        await process.wait()

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
    ) -> Any:
        """发送 JSON-RPC 请求并等待响应（单传输忽略 channel——连接池路由参数）"""
        self._request_id += 1
        request_id = self._request_id

        message = {
            "id": request_id,
            "method": method,
            "params": params or {},
        }

        future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future

        try:
            await self._write_message(message)
            return await asyncio.wait_for(future, timeout=self.request_timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise TimeoutError(f"request {method} timed out") from None
        except Exception as e:
            self._pending.pop(request_id, None)
            # 业务错误（JSON-RPC error 响应的 ProtocolError、连接已断）原样透传，
            # 只把传输层发送失败包装为 ConnectionError
            if isinstance(e, ExecutorError):
                raise
            raise ConnectionError(f"failed to send request {method}: {e}") from e

    async def send_notification(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
    ) -> None:
        """发送 JSON-RPC 通知（无响应；单传输忽略 channel——连接池路由参数）"""
        message = {
            "method": method,
            "params": params or {},
        }
        await self._write_message(message)

    async def _write_message(self, message: dict) -> None:
        """写一条 NDJSON 消息到子进程 stdin"""
        process = self._process
        if (
            process is None
            or self._closed
            or process.stdin is None
            or process.stdin.is_closing()
        ):
            raise ConnectionError("not connected")
        line = (
            json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode()
        if len(line) > self.max_message_size:
            raise ProtocolError(
                f"message exceeds maximum stdio length of {self.max_message_size} bytes"
            )
        try:
            process.stdin.write(line)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as e:
            self._fail_pending()
            self._closed = True
            raise ConnectionError(f"failed to write to process stdin: {e}") from e

    def on_notification(self, handler: Callable[[dict], Awaitable[None]]) -> None:
        """注册通知处理器"""
        self._notification_handlers.append(handler)

    async def _receive_loop(self) -> None:
        """读子进程 stdout 的 NDJSON 消息循环"""
        process = self._process
        assert process is not None and process.stdout is not None
        try:
            while not self._closed:
                line = await process.stdout.readline()
                if not line:
                    break  # EOF：进程退出或管道关闭
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if "id" in message and message["id"] is not None:
                    # 响应
                    request_id = message["id"]
                    future = self._pending.pop(request_id, None)
                    if future and not future.done():
                        if "error" in message:
                            error = message["error"]
                            future.set_exception(
                                ProtocolError(
                                    f"JSON-RPC error {error.get('code')}: {error.get('message')}"
                                )
                            )
                        else:
                            future.set_result(message.get("result"))
                elif "method" in message:
                    # 通知
                    for handler in self._notification_handlers:
                        try:
                            await handler(message)
                        except Exception:
                            pass
        except (ValueError, OSError) as e:
            # ValueError：单行超过 max_message_size；OSError：管道读取失败
            logger.warning("stdio receive loop failed: %s", e)
        finally:
            self._closed = True
            self._fail_pending()

    async def _stderr_loop(self) -> None:
        """持续消费子进程 stderr，防止缓冲填满死锁"""
        process = self._process
        assert process is not None and process.stderr is not None
        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                text = line.decode(errors="replace").rstrip()
                if self.stderr_handler is not None:
                    try:
                        self.stderr_handler(text)
                    except Exception:
                        pass
                else:
                    logger.debug("nova-executor stdio stderr: %s", text)
        except (ValueError, OSError):
            pass

    async def _wait_loop(self) -> None:
        """进程退出传播为连接断开（receive 循环的 EOF 是主路径，这里兜底）"""
        process = self._process
        assert process is not None
        returncode = await process.wait()
        if not self._closed:
            logger.warning("nova-executor stdio process exited: %s", returncode)
            self._closed = True
            self._fail_pending()

    def _fail_pending(self) -> None:
        """连接断开收尾：取消所有 pending 请求（幂等）"""
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("connection closed"))
        self._pending.clear()

    @property
    def is_connected(self) -> bool:
        return (
            self._process is not None
            and not self._closed
            and self._process.returncode is None
        )
