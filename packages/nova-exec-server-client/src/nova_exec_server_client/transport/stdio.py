"""stdio 传输层：spawn 子进程，stdin/stdout 承载 NDJSON JSON-RPC

（本地 `nova-exec-server --listen stdio` 或 SSH 远程同一实现）

边界处理（重写保留项，勿删）：
- stdio 子进程 spawn 时独立进程组（process_group=0，pgid == 子进程 pid），
  关闭逐级收割进程树：关 stdin 等 EOF 宽限 → terminate 进程组 → kill
  进程组（对位 Rust connection.rs 的 terminate_process_tree /
  kill_process_tree——沙箱 helper 等孙进程随组一并收割，不得漏杀；
  Windows 对位 kill_windows_process_tree：taskkill /PID <pid> /T /F
  杀整树，失败落直接子兜底；Windows spawn 经 CREATE_NO_WINDOW 不分配
  控制台窗口，对位 Rust background_command）
- stdio spawn 的合成 env（继承 + 显式叠加双侧）经
  scrub_non_inheritable_env_vars 剔除启动上下文敏感变量（对位 codex
  protocol shell_environment.rs 的 NON_INHERITABLE_ENV_VARS 名单，
  大小写不敏感比较）
- stdio 子进程 stderr 由独立任务按 8KiB 分块持续消费（对位 Rust
  client_transport.rs 的 take() 分块读——行长无上限，超长行分块分发，
  泵永不停），防缓冲填满死锁
- stdio 单条 NDJSON 消息 64MB 上限（对齐服务端 MAX_STDIO_JSONRPC_MESSAGE_LEN；
  StreamReader 行限 +2 为 CRLF 前瞻余量——对位 Rust connection.rs 的
  max+1+CRLF 前瞻：恰好上限的消息带 CRLF 终止也收得下，超限由读回后的
  长度检查拒）
- 意外断线即收割底层承载（对位 Rust RpcClient::drop / 连接退役即
  transport.terminate()）：stdio 后台组杀旧进程树——恢复层只造新候选，
  旧承载（含 stdout EOF 但进程存活、stdin 写失败等路径下的残留进程）
  由本层收尾
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
from collections.abc import Callable
from typing import Any

from ..errors import ConnectionError
from .base import _JsonRpcTransport
from .gate import _gate_inbound_message

logger = logging.getLogger(__name__)

#: spawn stdio 子进程时须从合成 env（继承 + 显式覆盖双侧）剔除的变量名单
#: （对位 codex protocol shell_environment.rs 的 NON_INHERITABLE_ENV_VARS，
#: 逐字照抄；大小写不敏感比较——对位 eq_ignore_ascii_case）：防止
#: model 可达的子进程继承 Codex 启动上下文
_NON_INHERITABLE_ENV_VARS = frozenset(
    {
        "CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN",
        "NODE_REPL_AUTH_TOKEN",
        "CODEX_GUARDIAN_DECISIONS_API_KEY",
        "OPENAI_FEDERATION_RULE_ID",
        "OPENAI_IDENTITY_TOKEN_FILE",
        "OPENAI_WORKLOAD_IDENTITY_CONTEXT",
    }
)

#: Windows 后台进程不分配控制台窗口的标志（对位 Rust background_command 的
#: CREATE_NO_WINDOW）；非 Windows 平台的 subprocess 无此常量——该值只在
#: nt 分支使用，取 0 占位（等同不传标志）
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _kill_windows_process_tree(pid: int) -> bool:
    """taskkill 杀整棵进程树（对位 Rust connection.rs 的
    kill_windows_process_tree：/T 整棵子树、/F 强制；stdin/stdout/stderr
    全置 null）。返回 False 时调用方落直接子兜底（对位 kill_direct_child）"""
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )
    except OSError as e:
        logger.warning(
            "failed to run taskkill for exec-server stdio process tree %s: %s",
            pid,
            e,
        )
        return False
    return result.returncode == 0


class StdioTransport(_JsonRpcTransport):
    """stdio 传输层：spawn 子进程，stdin/stdout 承载 NDJSON JSON-RPC（一行一条消息）。

    command 参数化对齐 codex 的 `StdioExecServerCommand`（program + args + env + cwd），
    本地与 SSH 远程同一实现——调用方换 command 即可，不为 SSH 写专门类：

    - 本地：`StdioTransport()`（默认 `nova-exec-server --listen stdio`）
    - SSH：`StdioTransport(program="ssh", args=["user@host", "nova-exec-server", "--listen", "stdio"])`

    注意：stderr 由独立任务持续消费（打日志或回调），防止子进程 stderr
    缓冲填满死锁；进程退出传播为连接断开（pending 请求以 ConnectionError 收尾，
    并触发 on_disconnect——恢复层据此重 spawn）。子进程 spawn 时入独立进程组
    （POSIX；Windows 经 CREATE_NO_WINDOW 不分配控制台），关闭/意外断线逐级
    收割整个进程树（POSIX 组杀；Windows taskkill /T /F 杀整树）。
    """

    #: 默认命令：本地 nova-exec-server 的 stdio 监听模式
    DEFAULT_PROGRAM = "nova-exec-server"
    DEFAULT_ARGS = ("--listen", "stdio")
    #: 单条 NDJSON 消息上限，与服务端 MAX_STDIO_JSONRPC_MESSAGE_LEN 对齐
    MAX_MESSAGE_SIZE = 64 * 1024 * 1024
    #: 关闭 stdin 后等待进程自行退出的宽限期（对齐服务端 2s 终止宽限）
    _SHUTDOWN_GRACE = 2.0
    #: stderr 泵的分块读取尺寸（对位 Rust MAX_STDIO_STDERR_LOG_LINE_LEN）
    _STDERR_READ_CHUNK = 8 * 1024

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
        #: 意外断线后的后台进程树收割任务（disconnect 时一并收尾）
        self._reap_task: asyncio.Task | None = None

    async def connect(self) -> None:
        """spawn 子进程并启动收发循环"""
        # 对齐 Rust stdio_command_process：env 叠加在继承环境之上，而非整体
        # 替换；合成 env（继承 + 显式覆盖双侧）再经
        # scrub_non_inheritable_env_vars 剔除启动上下文敏感变量（大小写不敏感，
        # 对位 eq_ignore_ascii_case）
        env = {
            name: value
            for name, value in {**os.environ, **(self.env or {})}.items()
            if name.upper() not in _NON_INHERITABLE_ENV_VARS
        }
        spawn_kwargs: dict[str, Any] = {}
        if os.name == "posix":
            # 独立进程组（pgid == 子进程 pid；对位 Rust client_transport.rs 的
            # process_group(0)）——关闭/断线时 os.killpg 组杀依赖此前提
            spawn_kwargs["process_group"] = 0
        elif os.name == "nt":
            # 后台子进程不分配控制台窗口（对位 Rust client_transport.rs 经
            # background_command 构造的 CREATE_NO_WINDOW）
            spawn_kwargs["creationflags"] = _CREATE_NO_WINDOW
        try:
            self._process = await asyncio.create_subprocess_exec(
                self.program,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=env,
                # StreamReader 行缓冲上限：服务端单条消息可达 64MB；+2 为 CRLF
                # 前瞻余量（对位 Rust connection.rs 的 max+1+CRLF 前瞻——恰好
                # 上限的消息带 CRLF 终止也收得下，超限由读回后的 len 检查拒）
                limit=self.max_message_size + 2,
                **spawn_kwargs,
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
        """断开连接：关 stdin 让服务端自行退出，宽限后 terminate/kill 进程组
        兜底（主动关闭——不触发 on_disconnect）"""
        self._closed = True
        self._cancel_inbound_tasks()
        for task in (self._receive_task, self._stderr_task, self._wait_task):
            if task:
                task.cancel()

        process = self._process
        if process is not None:
            if self._reap_task is not None:
                # 意外断线已起后台收割（同一升级序列、同一进程），等它收尾即可
                try:
                    await self._reap_task
                except Exception:
                    pass
                self._reap_task = None
            else:
                self._close_stdin(process)
                await self._reap_process_tree(process)
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
        await self._drain_inbound_tasks()
        self._fail_pending()

    @staticmethod
    def _close_stdin(process: asyncio.subprocess.Process) -> None:
        """关 stdin：服务端读 EOF 即结束连接并自行退出（优雅路径；已关/已坏幂等）"""
        stdin = process.stdin
        if stdin is not None and not stdin.is_closing():
            try:
                stdin.close()
            except Exception:
                pass

    async def _reap_process_tree(self, process: asyncio.subprocess.Process) -> None:
        """等待进程树退出，逐级升级：EOF 宽限 → terminate 进程组 → kill 进程组
        （对位 Rust terminate_stdio_child + 监督器的 wait → kill_process_tree）。

        直接子已自行退出时仍对组补一发 kill——收割可能存活的孙进程（对位
        Rust 监督器在 child wait 完成后无条件 kill_process_tree）。
        """
        try:
            await asyncio.wait_for(process.wait(), timeout=self._SHUTDOWN_GRACE)
        except asyncio.TimeoutError:
            pass
        else:
            self._kill_process_tree(process)
            return
        self._terminate_process_tree(process)
        try:
            await asyncio.wait_for(process.wait(), timeout=self._SHUTDOWN_GRACE)
        except asyncio.TimeoutError:
            pass
        self._kill_process_tree(process)
        await process.wait()

    @staticmethod
    def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
        """SIGTERM 到整个进程组（对位 Rust terminate_process_tree 的 unix
        分支）；Windows 走 taskkill 杀整树（对位 kill_windows_process_tree），
        组杀/taskkill 失败回退直接子（对位 kill_direct_child 兜底）"""
        if os.name == "posix":
            try:
                # pgid == 直接子 pid（spawn 时 process_group=0）
                os.killpg(process.pid, signal.SIGTERM)
                return
            except ProcessLookupError:
                return  # 组已整体退出
            except OSError as e:
                logger.warning(
                    "failed to terminate exec-server stdio process group %s: %s",
                    process.pid,
                    e,
                )
        elif os.name == "nt":
            if _kill_windows_process_tree(process.pid):
                return
        try:
            process.terminate()
        except ProcessLookupError:
            pass

    @staticmethod
    def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
        """SIGKILL 到整个进程组兜底（对位 Rust kill_process_tree 的 unix
        分支）；Windows 走 taskkill 杀整树，失败回退直接子"""
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
                return
            except ProcessLookupError:
                return
            except OSError as e:
                logger.warning(
                    "failed to kill exec-server stdio process group %s: %s",
                    process.pid,
                    e,
                )
        elif os.name == "nt":
            if _kill_windows_process_tree(process.pid):
                return
        try:
            process.kill()
        except ProcessLookupError:
            pass

    def _abort_connection(self) -> None:
        """收割旧子进程树（对位 Rust 连接退役即 transport.terminate()）——
        恢复层只造新候选不重连旧传输：stdout EOF 但进程存活、stdin 写失败等
        路径下，旧进程及其沙箱 helper 孙进程必须由本层组杀，不得残留"""
        process = self._process
        if process is None or self._reap_task is not None:
            return
        self._reap_task = asyncio.create_task(self._reap_abandoned(process))

    async def _reap_abandoned(self, process: asyncio.subprocess.Process) -> None:
        """后台收割被遗弃的子进程（与 disconnect 同一升级序列；失败仅留痕——
        断线收尾不依赖收割结果）"""
        try:
            self._close_stdin(process)
            await self._reap_process_tree(process)
        except Exception:
            logger.warning(
                "reaping abandoned exec-server process failed", exc_info=True
            )

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
            # allow_nan=False：NaN/Infinity 非合法 JSON（serde_json 序列化非
            # 有限浮点为 null；py 的 json 默认印裸 NaN，服务端词法拒收判连接
            # 级致命）——序列化失败本地抛错不上线
            json.dumps(
                message, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            )
            + "\n"
        ).encode()
        # 出站不做本地限长（对位基准 connection.rs 直发）——上限执法归
        # 服务端单点，超限由服务端杀连接、恢复兜底
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
                if line.endswith(b"\n"):
                    line = line[:-1]
                    if line.endswith(b"\r"):
                        line = line[:-1]
                if len(line) > self.max_message_size:
                    reason = (
                        "JSON-RPC message from exec-server exceeds maximum "
                        f"length of {self.max_message_size} bytes"
                    )
                    break
                if not line.strip():
                    continue
                message, fatal = _gate_inbound_message(line)
                if fatal is not None:
                    reason = fatal
                    break

                if "method" in message:
                    if message.get("id") is not None:
                        # 服务端反向请求（裁决等）：准入 + 并发执行，接收循环不堵
                        self._dispatch_server_request(message)
                        # 准入违规已标记断线时 while 条件即收尾退出
                    else:
                        await self._run_notification_handlers(message)
                else:
                    # 响应同步归 future（接收循环不被处理器阻塞）
                    self._resolve_response(message)
        except (ValueError, OSError) as e:
            # ValueError：单行超过 max_message_size；OSError：管道读取失败
            reason = str(e)
            logger.warning("stdio receive loop failed: %s", e)
        finally:
            if not self._closed:
                self._mark_disconnected(reason)

    async def _stderr_loop(self) -> None:
        """持续消费子进程 stderr，防止缓冲填满死锁（对位 Rust
        client_transport.rs 的 take(8KiB) + read_until 分块读——行长无上限：
        超长行攒满一块即先行分发，泵永不因行长停转）"""
        process = self._process
        assert process is not None and process.stderr is not None
        pending = b""
        try:
            while True:
                chunk = await process.stderr.read(self._STDERR_READ_CHUNK)
                if not chunk:
                    break  # EOF：进程退出或管道关闭
                pending += chunk
                # 完整行逐条分发
                while True:
                    line, sep, rest = pending.partition(b"\n")
                    if not sep:
                        break
                    self._emit_stderr_line(line)
                    pending = rest
                # 超长行：攒满一块即先行分发（对位 take 限长语义）
                while len(pending) >= self._STDERR_READ_CHUNK:
                    self._emit_stderr_line(pending[: self._STDERR_READ_CHUNK])
                    pending = pending[self._STDERR_READ_CHUNK :]
            if pending:
                self._emit_stderr_line(pending)
        except OSError as e:
            logger.warning("failed to read exec-server stdio stderr: %s", e)

    def _emit_stderr_line(self, raw: bytes) -> None:
        """stderr 一行/一块的统一出口：lossy 解码后回调或打日志"""
        text = raw.decode(errors="replace")
        if text.endswith("\r"):
            text = text[:-1]
        if self.stderr_handler is not None:
            try:
                self.stderr_handler(text)
            except Exception:
                logger.warning("stdio stderr handler raised", exc_info=True)
        else:
            logger.debug("nova-exec-server stdio stderr: %s", text)

    async def _wait_loop(self) -> None:
        """进程退出传播为连接断开（receive 循环的 EOF 是主路径，这里兜底）"""
        process = self._process
        assert process is not None
        returncode = await process.wait()
        if not self._closed:
            logger.warning("nova-exec-server stdio process exited: %s", returncode)
            self._mark_disconnected(f"process exited with code {returncode}")

    @property
    def is_connected(self) -> bool:
        return (
            self._process is not None
            and not self._closed
            and self._process.returncode is None
        )
