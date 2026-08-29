"""传输层：连接管理与 JSON-RPC 消息收发

- `WebSocketTransport`：ws:// / wss:// 远程连接（websockets asyncio 新 API）
- `StdioTransport`：spawn 子进程，stdin/stdout 承载 NDJSON JSON-RPC
  （本地 `nova-executor --listen stdio` 或 SSH 远程同一实现）
- `Transport`：两种传输与连接池（pool.TransportPool）、恢复包装
  （recovery.ManagedTransport）共同遵守的接口协议，上层只面向它编程

两种传输共享 `_JsonRpcTransport` 基类：pending 请求表、响应/通知分发、
断线回调（`on_disconnect`——恢复层据此发起重连，对位 Rust 的
RpcClientEvent::Disconnected 驱动 request_recovery）。

边界处理（重写保留项，勿删）：
- stdio 子进程 stderr 由独立任务持续消费，防缓冲填满死锁
- stdio 断开逐级 reap：关 stdin 等 EOF 宽限 → terminate → kill
- stdio 单条 NDJSON 消息 64MB 上限（对齐服务端 MAX_STDIO_JSONRPC_MESSAGE_LEN）
- 写路径 drain 提供天然背压（对端读慢时发送方挂起而非内存膨胀）
- 连接断开传播：pending 请求一律以 ConnectionError 收尾
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from abc import abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import websockets.asyncio.client
import websockets.exceptions

from .errors import (
    AuthError,
    ConnectionError,
    ExecutorError,
    ProtocolError,
    TimeoutError,
)

logger = logging.getLogger(__name__)

#: 通知处理器 / 断线回调 / stderr 回调的签名
NotificationHandler = Callable[[dict], Awaitable[None]]
DisconnectHandler = Callable[[str | None], None]


class Transport(Protocol):
    """传输层接口协议（结构化类型——实现类无需显式继承）。

    `channel` 参数只在连接池（TransportPool）上有路由意义；单传输实现
    接收但忽略它，保持接口统一。

    `on_disconnect` 注册意外断线回调（主动 disconnect 不触发）——恢复层
    （recovery.ManagedTransport）据此发起重连；不参与恢复的调用方可忽略。
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

    def on_notification(self, handler: NotificationHandler) -> None: ...

    def on_disconnect(self, handler: DisconnectHandler) -> None: ...

    @property
    def is_connected(self) -> bool: ...


class _JsonRpcTransport:
    """JSON-RPC 收发公共机械：pending 表、消息分发、断线传播。

    子类实现 `_send_message`（写线）并启动各自的接收循环；接收循环逐条
    调 `_resolve_response` / `_run_notification_handlers`，意外结束时调
    `_mark_disconnected`。
    """

    def __init__(self, request_timeout: float):
        self.request_timeout = request_timeout
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._notification_handlers: list[NotificationHandler] = []
        self._disconnect_handlers: list[DisconnectHandler] = []
        self._closed = True  # 未连接即关闭态

    # ------------------------------------------------------------------
    # 发送
    # ------------------------------------------------------------------

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

        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future

        try:
            await self._send_message(message)
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
        await self._send_message(
            {
                "method": method,
                "params": params or {},
            }
        )

    @abstractmethod
    async def _send_message(self, message: dict) -> None:
        """把一条 JSON-RPC 消息写到线上（子类实现各自的线格式）"""

    # ------------------------------------------------------------------
    # 接收分发
    # ------------------------------------------------------------------

    def _resolve_response(self, message: dict) -> None:
        """响应归 pending future：error 响应映射为带结构化 code 的 ProtocolError"""
        request_id = message["id"]
        future = self._pending.pop(request_id, None)
        if future is None or future.done():
            return
        if "error" in message:
            error = message["error"]
            code = error.get("code")
            future.set_exception(
                ProtocolError(
                    f"JSON-RPC error {code}: {error.get('message')}",
                    code=code,
                )
            )
        else:
            future.set_result(message.get("result"))

    async def _run_notification_handlers(self, message: dict) -> None:
        """通知按序分发给全部处理器（异常逐条 logger.warning 留痕——
        处理器错误不许炸掉接收循环，也不静默吞）"""
        for handler in self._notification_handlers:
            try:
                await handler(message)
            except Exception:
                logger.warning(
                    "notification handler raised on %s",
                    message.get("method"),
                    exc_info=True,
                )

    def on_notification(self, handler: NotificationHandler) -> None:
        """注册通知处理器"""
        self._notification_handlers.append(handler)

    # ------------------------------------------------------------------
    # 断线
    # ------------------------------------------------------------------

    def on_disconnect(self, handler: DisconnectHandler) -> None:
        """注册意外断线回调（同步回调；主动 disconnect 不触发）"""
        self._disconnect_handlers.append(handler)

    def _mark_disconnected(self, reason: str | None) -> None:
        """意外断线的统一收尾：pending 以 ConnectionError 收尾并触发
        on_disconnect 回调（主动 disconnect 不走这里；多路径并发只生效一次）"""
        if self._closed:
            return
        self._closed = True
        self._fail_pending()
        for handler in self._disconnect_handlers:
            try:
                handler(reason)
            except Exception:
                logger.warning("disconnect handler raised", exc_info=True)

    def _fail_pending(self) -> None:
        """连接断开收尾：取消所有 pending 请求（幂等）"""
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("connection closed"))
        self._pending.clear()


class WebSocketTransport(_JsonRpcTransport):
    """WebSocket 传输层，负责连接管理和 JSON-RPC 消息收发"""

    def __init__(
        self,
        url: str,
        token: str | None = None,
        max_payload: int = 100 * 1024 * 1024,
        request_timeout: float = 30.0,
    ):
        super().__init__(request_timeout)
        self.url = url
        self.token = token
        self.max_payload = max_payload
        self._ws: websockets.asyncio.client.ClientConnection | None = None
        self._receive_task: asyncio.Task | None = None

    async def connect(self) -> None:
        """建立 WebSocket 连接"""
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            self._ws = await websockets.asyncio.client.connect(
                self.url,
                additional_headers=headers,
                max_size=self.max_payload,
            )
        except websockets.exceptions.InvalidStatus as e:
            if e.response.status_code == 401:
                raise AuthError(
                    "authentication failed: invalid or missing token"
                ) from e
            raise ConnectionError(f"failed to connect to {self.url}: {e}") from e
        except Exception as e:
            raise ConnectionError(f"failed to connect to {self.url}: {e}") from e

        self._closed = False
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def disconnect(self) -> None:
        """断开连接（主动关闭——不触发 on_disconnect）"""
        self._closed = True
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None
        self._fail_pending()

    async def _send_message(self, message: dict) -> None:
        if self._ws is None or self._closed:
            raise ConnectionError("not connected")
        await self._ws.send(json.dumps(message))

    async def _receive_loop(self) -> None:
        """接收消息循环；循环意外结束（对端关闭/读失败）即断线"""
        reason: str | None = None
        try:
            assert self._ws is not None
            async for raw in self._ws:
                if self._closed:
                    return  # 主动 disconnect：收尾归 disconnect()，不触发回调
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if "id" in message and message["id"] is not None:
                    self._resolve_response(message)
                elif "method" in message:
                    await self._run_notification_handlers(message)
        except websockets.exceptions.ConnectionClosed as e:
            reason = str(e)
        except Exception as e:  # 读路径其他失败同样传播为断线
            reason = str(e)
            logger.warning("websocket receive loop failed: %s", e)
        finally:
            if not self._closed:
                self._mark_disconnected(reason)

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not self._closed


class StdioTransport(_JsonRpcTransport):
    """stdio 传输层：spawn 子进程，stdin/stdout 承载 NDJSON JSON-RPC（一行一条消息）。

    command 参数化对齐 codex 的 `StdioExecServerCommand`（program + args + env + cwd），
    本地与 SSH 远程同一实现——调用方换 command 即可，不为 SSH 写专门类：

    - 本地：`StdioTransport()`（默认 `nova-executor --listen stdio`）
    - SSH：`StdioTransport(program="ssh", args=["user@host", "nova-executor", "--listen", "stdio"])`

    注意：stderr 由独立任务持续消费（打日志或回调），防止子进程 stderr
    缓冲填满死锁；进程退出传播为连接断开（pending 请求以 ConnectionError 收尾，
    并触发 on_disconnect——恢复层据此重 spawn）。
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
        super().__init__(request_timeout)
        self.program = program
        self.args = list(args)
        self.env = dict(env) if env else None
        self.cwd = cwd
        self.stderr_handler = stderr_handler
        self.max_message_size = max_message_size
        self._process: asyncio.subprocess.Process | None = None
        self._receive_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._wait_task: asyncio.Task | None = None

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
        """断开连接：关 stdin 让服务端自行退出，宽限后 terminate/kill 兜底
        （主动关闭——不触发 on_disconnect）"""
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

    async def _send_message(self, message: dict) -> None:
        """写一条 NDJSON 消息到子进程 stdin（drain 提供背压）"""
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
            self._mark_disconnected(f"stdin write failed: {e}")
            raise ConnectionError(f"failed to write to process stdin: {e}") from e

    async def _receive_loop(self) -> None:
        """读子进程 stdout 的 NDJSON 消息循环"""
        process = self._process
        assert process is not None and process.stdout is not None
        reason: str | None = None
        try:
            while not self._closed:
                line = await process.stdout.readline()
                if not line:
                    reason = "process closed stdout"
                    break  # EOF：进程退出或管道关闭
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if "id" in message and message["id"] is not None:
                    # 响应同步归 future（接收循环不被处理器阻塞）
                    self._resolve_response(message)
                elif "method" in message:
                    await self._run_notification_handlers(message)
        except (ValueError, OSError) as e:
            # ValueError：单行超过 max_message_size；OSError：管道读取失败
            reason = str(e)
            logger.warning("stdio receive loop failed: %s", e)
        finally:
            if not self._closed:
                self._mark_disconnected(reason)

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
                        logger.warning("stdio stderr handler raised", exc_info=True)
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
            self._mark_disconnected(f"process exited with code {returncode}")

    @property
    def is_connected(self) -> bool:
        return (
            self._process is not None
            and not self._closed
            and self._process.returncode is None
        )
