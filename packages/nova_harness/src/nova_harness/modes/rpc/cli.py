"""Command-line entry point for nova-harness-rpc."""

import argparse
import asyncio
import signal
import sys

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
        description="Nova Harness JSON-RPC server（stdio——TUI 子进程形态）",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
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
    _build_parser().parse_args()
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

        signals = [signal.SIGINT, signal.SIGTERM]
        if sys.platform != "win32":
            signals.append(signal.SIGHUP)
        for sig in signals:
            try:
                loop.add_signal_handler(sig, _on_signal, sig)
            except NotImplementedError:
                # asyncio 在 Windows 仅支持 SIGINT 注册——不支持的信号静默
                # 降级（关停语义由 stdin EOF / shutdown 命令兜底）
                pass

        try:
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


def main() -> int:
    """Synchronous entry point for console scripts."""
    try:
        return asyncio.run(_async_main())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
