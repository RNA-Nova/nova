"""executor 客户端生命周期管理（薄适配层——客户端生命周期归 SDK
EnvironmentManager，本模块只留 nova 胶水）。

批次 B 归位：连接管理词汇归 executor 栈自持（SDK 的
``EnvironmentManager``——懒创建/缓存/状态观察/增删/清扫，对位 codex
environments 体系）。本模块保留的三件 nova 特有胶水：

- **本地 spawn**：解析 nova-executor 二进制并 spawn 回环实例
  （ws://127.0.0.1:0 + 随机 bearer token）——spawn 后作为 ws 环境
  动态注册进 SDK 注册表；
- **SSH 供给**：经 provision 供给远程实例 + 回环隧道（隧道死亡
  重新供给 + 换注册——对应 SDK 的 refresh 语义）；
- **进程级清理**：atexit 回收隧道与 spawn 的 executor。

二进制解析链：``NOVA_EXECUTOR_BIN`` 环境变量 → nova 托管 bin
（~/.nova/agent/bin/）→ 仓库本地构建（target/release|debug，开发态）→ PATH。
"""

from __future__ import annotations

import asyncio
import atexit
import os
import secrets
import subprocess
from pathlib import Path
from typing import Awaitable, Callable, Optional

from nova_coding_agent.executor.provision import (
    BootstrapFn,
    ProgressFn,
    SshRemoteHandle,
    SshTarget,
    is_ssh_url,
    parse_ssh_target,
    provision,
)
from nova_exec_server_client import (
    EnvironmentManager,
    ExecutorClient,
    ExecutorConfig,
    ExecutorEnvironment,
)
from nova_harness.config.defaults import get_agent_dir

# 等待本地 executor 打印监听地址的超时
_SPAWN_READY_TIMEOUT_S = 10.0


def resolve_executor_binary() -> Optional[str]:
    """解析 nova-executor 二进制路径（四级链）。"""
    env_bin = os.environ.get("NOVA_EXECUTOR_BIN")
    if env_bin and os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
        return env_bin

    managed = Path(get_agent_dir()) / "bin" / "nova-executor"
    if managed.is_file() and os.access(managed, os.X_OK):
        return str(managed)

    # 开发态兜底：仓库内 cargo 构建产物
    repo = Path(__file__).resolve()
    for parent in repo.parents:
        candidate = parent / "packages" / "nova_exec_server_client"
        if candidate.is_dir():
            for profile in ("release", "debug"):
                built = candidate / "target" / profile / "nova-executor"
                if built.is_file() and os.access(built, os.X_OK):
                    return str(built)
            break

    # PATH 兜底
    from shutil import which

    return which("nova-executor")


class ExecutorClientManager:
    """executor 客户端生命周期（进程级单例——薄适配层）。

    客户端缓存/连接生命周期全归 SDK ``EnvironmentManager``；本类持有
    nova 胶水（本地 spawn 的 daemon 进程、SSH 隧道句柄）并把动态端点
    注册为环境。后端进程是一会话一进程，模块级状态即会话级。
    """

    def __init__(self) -> None:
        self._env_manager = EnvironmentManager(ExecutorConfig())
        self._spawned: list[subprocess.Popen] = []
        self._ssh_handles: dict[str, SshRemoteHandle] = {}
        self._lock = asyncio.Lock()
        self._atexit_registered = False

    def _ensure_atexit(self) -> None:
        """首个后端进程/隧道创建时挂 atexit 清理（幂等）。

        后端进程正常退出（含 RPC 服务器信号优雅关停后的解释器退出）时
        回收 ssh 隧道子进程与本地 spawn 的 executor——python 一死它们
        即成孤儿（远程 executor 随之长存，实证过）。SIGKILL 无解
        （挂账：executor 侧空闲超时自毁）。
        """
        if self._atexit_registered:
            return
        self._atexit_registered = True
        atexit.register(self._sync_cleanup)

    def _sync_cleanup(self) -> None:
        """atexit 同步清理：终止隧道 ssh 与本地 spawn 的 executor。"""
        for handle in self._ssh_handles.values():
            handle.stop()
        self._ssh_handles.clear()
        for proc in self._spawned:
            try:
                proc.terminate()
            except Exception:
                pass
        self._spawned.clear()

    async def get_client(self, url: Optional[str] = None) -> ExecutorClient:
        """获取客户端：url=None → 本地 spawn；ssh:// → SSH 隧道；其余直连。

        连接生命周期全在 SDK EnvironmentManager（缓存命中直返）；SSH 隧道
        死亡（断网/休眠/远端重启）→ 重新供给并以新端点换注册。
        """
        key = url or "__local__"
        async with self._lock:
            handle = self._ssh_handles.get(key)
            if handle is not None and not handle.alive():
                # 隧道死亡：重供给 + 换注册（等价 SDK refresh 的供给侧）
                self._ssh_handles.pop(key, None)
                handle = None
            if url is None:
                status = self._env_manager.status("__local__")
                if status.state.value != "connected":
                    await self._register_local_spawn()
            elif is_ssh_url(url):
                if handle is None:
                    await self._register_ssh(parse_ssh_target(url))
            else:
                await self._env_manager.upsert_environment(
                    ExecutorEnvironment(id=key, url=url),
                    token=os.environ.get("NOVA_EXECUTOR_TOKEN"),
                )
            return await self._env_manager.get_client(key)

    async def provision_ssh(
        self,
        target: SshTarget,
        on_progress: Optional[ProgressFn] = None,
        bootstrap: Optional[BootstrapFn] = None,
    ) -> ExecutorClient:
        """/executor 命令的 eagerly 供给入口（带进度与首连引导回调）。

        已供给且隧道活着时直接复用——幂等，/executor 重选同主机零开销。
        """
        async with self._lock:
            handle = await self._ensure_ssh_handle(target, on_progress, bootstrap)
            await self._env_manager.upsert_environment(
                ExecutorEnvironment(id=handle.target.canonical_url, url=handle.url),
                token=handle.token,
            )
            return await self._env_manager.get_client(handle.target.canonical_url)

    async def _register_ssh(self, target: SshTarget) -> None:
        """懒路径注册：BatchMode 供给（免密应已就绪——首连引导归 /executor 命令）。"""
        handle = await self._ensure_ssh_handle(target)
        await self._env_manager.upsert_environment(
            ExecutorEnvironment(id=handle.target.canonical_url, url=handle.url),
            token=handle.token,
        )

    async def _register_local_spawn(self) -> None:
        """本地 spawn 一次并注册为 ws 环境（随机 bearer 进 token 侧表）。"""
        url, token = await self._spawn_local()
        await self._env_manager.upsert_environment(
            ExecutorEnvironment(id="__local__", url=url), token=token
        )

    def get_ssh_handle(self, target: SshTarget) -> Optional[SshRemoteHandle]:
        """已供给的 SSH 句柄（/executor 扩展读远程家目录/shell 定远程 cwd 用）。"""
        return self._ssh_handles.get(target.canonical_url)

    async def _ensure_ssh_handle(
        self,
        target: SshTarget,
        on_progress: Optional[ProgressFn] = None,
        bootstrap: Optional[BootstrapFn] = None,
    ) -> SshRemoteHandle:
        key = target.canonical_url
        handle = self._ssh_handles.get(key)
        if handle is not None and handle.alive():
            return handle
        if handle is not None:
            handle.stop()
        handle = await provision(target, on_progress=on_progress, bootstrap=bootstrap)
        self._ssh_handles[key] = handle
        self._ensure_atexit()
        return handle

    async def _spawn_local(self) -> tuple[str, str]:
        binary = resolve_executor_binary()
        if binary is None:
            raise FileNotFoundError(
                "未找到 nova-executor 二进制（NOVA_EXECUTOR_BIN / ~/.nova/agent/bin/ "
                "/ 仓库 target 构建 / PATH 均未命中）"
            )
        token = secrets.token_hex(16)
        proc = subprocess.Popen(
            [
                binary,
                "--listen",
                "ws://127.0.0.1:0",
                "--auth",
                "bearer",
                "--auth-token",
                token,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self._spawned.append(proc)
        self._ensure_atexit()
        url = await self._read_listen_url(proc)
        return url, token

    async def _read_listen_url(self, proc: subprocess.Popen) -> str:
        """从 executor stdout 读实际监听地址（端口 0 动态分配）。

        用 asyncio 读行（带总超时），避免阻塞 readline 无法中断的问题。
        """
        import re

        loop = asyncio.get_running_loop()
        deadline = loop.time() + _SPAWN_READY_TIMEOUT_S
        assert proc.stdout is not None
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), proc.stdout
        )
        while loop.time() < deadline:
            if proc.returncode is not None:
                raise RuntimeError(f"nova-executor 启动失败（exit {proc.returncode}）")
            try:
                line = await asyncio.wait_for(
                    reader.readline(), timeout=max(0.1, deadline - loop.time())
                )
            except asyncio.TimeoutError:
                break
            match = re.search(rb"ws://127\.0\.0\.1:(\d+)", line)
            if match:
                return f"ws://127.0.0.1:{match.group(1).decode()}"
        raise TimeoutError("nova-executor 启动超时（未打印监听地址）")

    async def close_all(self) -> None:
        """断开全部客户端（SDK 清扫）并终止 spawn 的 executor 子进程与 SSH 隧道。"""
        await self._env_manager.close_all()
        for handle in self._ssh_handles.values():
            handle.stop()
        self._ssh_handles.clear()
        for proc in self._self_spawned():
            try:
                proc.terminate()
            except Exception:
                pass
        self._spawned.clear()

    def _self_spawned(self) -> list[subprocess.Popen]:
        return self._spawned


# 进程级单例（后端进程 = 会话作用域）
_manager: Optional[ExecutorClientManager] = None


def get_executor_manager() -> ExecutorClientManager:
    global _manager
    if _manager is None:
        _manager = ExecutorClientManager()
    return _manager
