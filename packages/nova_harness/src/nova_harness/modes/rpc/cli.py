"""Command-line entry point for nova-harness-rpc."""

import argparse
import asyncio
import json
import signal
import sys
from pathlib import Path
from typing import Any

from nova_harness.core.rpc.connection import ConnectionOrigin
from nova_harness.core.rpc.protocol import MethodRegistry
from nova_harness.core.rpc.protocol.methods import (
    register_auth_methods,
    register_model_methods,
    register_package_methods,
    register_resources_methods,
    register_session_methods,
    register_settings_methods,
    register_system_methods,
    register_user_tools_methods,
)
from nova_harness.core.rpc.protocol.methods.state import ServerState
from nova_harness.core.rpc.server import RpcServer
from nova_harness.core.rpc.transport import StdioTransport
from nova_harness.core.utils.child_process import kill_tracked_detached_children
from nova_harness.core.utils.output_guard import OutputGuard


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nova-harness-rpc",
        description="Nova Harness JSON-RPC server（stdio / WebSocket）",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )
    parser.add_argument(
        "--listen",
        default="stdio://",
        help="监听形态：stdio://（默认，TUI 子进程）或 ws://HOST:PORT（多客户端）",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="WS 鉴权 token（缺省经 --token-file 或 ~/.nova/agent/rpc-server.json 自动生成）",
    )
    parser.add_argument(
        "--token-file",
        default=None,
        help="WS token 文件路径（JSON，含 token 字段；无则生成落盘 0600）",
    )
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        help="WS Origin 白名单（可重复；带 Origin 头的请求不在名单即 403）",
    )
    return parser


def _redirect_stderr_to_log() -> None:
    """把后端 stderr 重定向到日志文件（fd 级——warning/logging 全覆盖）。

    后端的 stderr 经管道被父进程（TUI）转发到终端——UserWarning/logging
    杂讯直接喷到画面上（reload 时尤甚——重复注册、path 依赖警告齐发）。
    重定向后诊断信息落在 ``~/.nova/agent/logs/rpc-stderr.log``（附加写），
    比终端残影更可查。失败静默（stderr 原样，不阻断启动）。

    接管条件：stderr 是进程原生对象（``sys.stderr is sys.__stderr__``）。
    pytest 等捕获框架会替换 sys.stderr——此时绝不 dup2 劫持 fd 2
    （会破坏宿主进程后续全部用例）。不看 isatty：管道场景 isatty=False
    但正是被转发的污染路径。
    """
    try:
        if sys.stderr is not sys.__stderr__:
            # pytest 等捕获框架会替换 sys.stderr——此时绝不 dup2 劫持 fd 2。
            # 不看 isatty：管道场景 isatty=False 但正是被父进程转发到终端的污染路径。
            return
        import os

        from nova_harness.core.config.defaults import get_agent_dir

        log_dir = os.path.join(str(get_agent_dir()), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = open(  # noqa: SIM115（进程生命周期句柄，随退出关闭）
            os.path.join(log_dir, "rpc-stderr.log"), "a", encoding="utf-8"
        )
        os.dup2(log_file.fileno(), sys.stderr.fileno())
    except Exception:
        pass


def build_rpc_methods(state: ServerState) -> MethodRegistry:
    """构造 RPC 模式使用的 JSON-RPC 方法注册表。"""
    registry = MethodRegistry()
    register_session_methods(registry, state)
    register_model_methods(registry, state)
    register_auth_methods(registry, state)
    register_resources_methods(registry, state)
    register_settings_methods(registry, state)
    register_user_tools_methods(registry, state)
    register_system_methods(registry, state)
    register_package_methods(registry, state)
    return registry


async def _async_main() -> int:
    args = _build_parser().parse_args()
    _redirect_stderr_to_log()

    with OutputGuard():
        # OutputGuard 仍接管 sys.stdout（杂散 print/日志拦到 stderr）；
        # 协议帧经 StdioTransport 的 StreamWriter 直写 fd，不经 guard 白名单
        state = ServerState()
        methods = build_rpc_methods(state)
        server = RpcServer(methods, state)

        loop = asyncio.get_running_loop()

        def _on_signal(signum: int) -> None:
            # 先清场 detached 子进程（对齐 pi：SIGHUP/SIGTERM 时 kill
            # 所有被跟踪的后台子进程，不留孤儿），再关停服务器
            kill_tracked_detached_children()
            server.shutdown()

        signals = [signal.SIGINT]
        if sys.platform != "win32":
            signals += [signal.SIGTERM, signal.SIGHUP]
        for sig in signals:
            try:
                loop.add_signal_handler(sig, _on_signal, sig)
            except NotImplementedError:
                # asyncio 在 Windows 仅支持 SIGINT 注册——不支持的信号静默
                # 降级（关停语义由 stdin EOF / shutdown 命令兜底）
                pass

        try:
            if args.listen.startswith("ws://"):
                await _serve_websocket(server, args.listen, args)
            else:
                # stdio 单客户端形态：连接关闭即进程退出（exit_on_close）
                await server.add_connection(
                    StdioTransport(),
                    origin=ConnectionOrigin.STDIO,
                    exit_on_close=True,
                )
                await server.run()
        finally:
            for sig in signals:
                try:
                    loop.remove_signal_handler(sig)
                except NotImplementedError:
                    pass  # Windows 同样不支持摘除（与注册侧同构降级）
            kill_tracked_detached_children()
            await state.dispose_runtime()
    return 0


async def _serve_websocket(server: RpcServer, listen: str, args: Any) -> None:
    """WS 多客户端形态：acceptor 每接入一条即 ``add_connection``。"""
    from nova_harness.core.config.defaults import get_agent_dir
    from nova_harness.core.rpc.transport.websocket import (
        WebSocketAcceptor,
        _is_loopback,
        provision_token,
    )

    authority = listen[len("ws://") :]
    host, _, port_text = authority.rpartition(":")
    if not host or not port_text.isdigit():
        raise SystemExit(f"--listen 需要 ws://HOST:PORT 形态，收到: {listen}")

    # 非 loopback 监听必须显式 token（--token/--token-file）——自动落盘的
    # 本地 token 只配 loopback；裸网监听用自动生成等于把密钥留在共享机
    if not _is_loopback(host) and not args.token and not args.token_file:
        raise SystemExit(
            f"拒绝在非 loopback 地址（{host}）无显式 token 启动——"
            "请加 --token 或 --token-file"
        )

    token, _token_path = provision_token(
        args.token,
        args.token_file,
        Path(get_agent_dir()) / "rpc-server.json",
    )

    async def _on_connection(transport: Any) -> None:
        await server.add_connection(transport, origin=ConnectionOrigin.WEBSOCKET)

    acceptor = WebSocketAcceptor(
        host,
        int(port_text),
        token=token,
        allow_origins=set(args.allow_origin),
        on_connection=_on_connection,
    )
    await acceptor.start()
    # 监听信息落盘（0600）：客户端读它接入（url + token 一处拿齐）
    info_path = Path(get_agent_dir()) / "rpc-server.json"
    info_path.write_text(
        json.dumps({"url": f"ws://{host}:{acceptor.port}", "token": token}, indent=2),
        encoding="utf-8",
    )
    info_path.chmod(0o600)
    print(  # stdout 在 WS 形态下不是协议通道，可印一行人读信息
        f"nova-harness-rpc listening on ws://{host}:{acceptor.port} "
        f"（token 见 {info_path}）",
        file=sys.stderr,
        flush=True,
    )

    try:
        await server.wait()
    finally:
        await acceptor.close()
        await server.stop()


def main() -> int:
    """Synchronous entry point for console scripts."""
    try:
        return asyncio.run(_async_main())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
