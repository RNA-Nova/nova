"""executor 客户端生命周期管理（薄适配层——客户端生命周期归 SDK
EnvironmentManager，本模块只留 nova 胶水）。

批次 B 归位：连接管理词汇归 executor 栈自持（SDK 的
``EnvironmentManager``——懒创建/缓存/状态观察/增删/清扫，对位 codex
environments 体系）。本模块只保留两件 nova 胶水：

- **本地 spawn**：解析 nova-executor 二进制并 spawn 回环实例
  （ws://127.0.0.1:0 + 随机 bearer token）——spawn 后作为 ws 环境
  动态注册进 SDK 注册表；
- **进程级清理**：atexit 回收 spawn 的 executor。

远程接入只消费既有 ws(s) 端点；Nova 不再提供 SSH 密钥引导、远程二进制
上传或 ``ssh://`` 隧道供给。通用 stdio command 环境仍由 SDK 配置层承载。

二进制解析链：``NOVA_EXECUTOR_BIN`` 环境变量 → nova 托管 bin
（~/.nova/agent/bin/）→ 仓库本地构建（target/release|debug，开发态）→ PATH。
"""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import os
import secrets
import subprocess
from pathlib import Path
from typing import Optional

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
    本地 spawn 的 daemon 进程并把动态端点注册为环境。后端进程是一会话
    一进程，模块级状态即会话级。
    """

    def __init__(self) -> None:
        self._env_manager = EnvironmentManager(ExecutorConfig())
        self._spawned: list[subprocess.Popen] = []
        self._lock = asyncio.Lock()
        self._atexit_registered = False

    def _ensure_atexit(self) -> None:
        """首个本地后端进程创建时挂 atexit 清理（幂等）。"""
        if self._atexit_registered:
            return
        self._atexit_registered = True
        atexit.register(self._sync_cleanup)

    def _sync_cleanup(self) -> None:
        """atexit 同步清理：终止本地 spawn 的 executor。"""
        for proc in self._spawned:
            try:
                proc.terminate()
            except Exception:
                pass
        self._spawned.clear()

    async def get_client(self, url: Optional[str] = None) -> ExecutorClient:
        """获取客户端：url=None → 本地 spawn；其余仅接受 ws(s) 直连。

        连接生命周期全在 SDK EnvironmentManager（缓存命中直返）。
        """
        key = url or "__local__"
        async with self._lock:
            if url is None:
                # status() 现为 async 实况探活（A16——探活失败即判
                # DISCONNECTED 触发重新 spawn，不再信缓存的 CONNECTED）
                status = await self._env_manager.status("__local__")
                if status.state.value != "connected":
                    await self._register_local_spawn()
            else:
                if not url.startswith(("ws://", "wss://")):
                    raise ValueError("executor 远程端点仅支持 ws:// 或 wss://")
                await self._env_manager.upsert_environment(
                    ExecutorEnvironment(id=key, url=url),
                    token=os.environ.get("NOVA_EXECUTOR_TOKEN"),
                )
            return await self._env_manager.get_client(key)

    async def _register_local_spawn(self) -> None:
        """本地 spawn 一次并注册为 ws 环境（随机 bearer 进 token 侧表）。"""
        url, token = await self._spawn_local()
        await self._env_manager.upsert_environment(
            ExecutorEnvironment(id="__local__", url=url), token=token
        )

    async def _spawn_local(self) -> tuple[str, str]:
        binary = resolve_executor_binary()
        if binary is None:
            raise FileNotFoundError(
                "未找到 nova-executor 二进制（NOVA_EXECUTOR_BIN / ~/.nova/agent/bin/ "
                "/ 仓库 target 构建 / PATH 均未命中）"
            )
        token = secrets.token_hex(16)
        # 服务端只存 token 的 SHA-256（capability-token 模式——对位
        # websocket-auth 的 TokenSha256 凭据源；原 token 只发客户端
        # （Authorization: Bearer），不经服务端配置落盘）。旗标对位
        # exec-server-cli 的 --ws-auth 族——旧 `--auth bearer/--auth-token`
        # 拼写在 CLI 不存在（clap 直接拒绝，spawn 必死）。
        token_sha256 = hashlib.sha256(token.encode("utf-8")).hexdigest()
        # 父死子随：显式注入 leash 环境变量，并把子进程 stdin 保持为父进程
        # 私管——父进程死亡/管道关闭时 executor 收到 EOF 退出（对位 codex
        # ParentLifetime::StdinPipe）。`_spawned` 持有 proc，stdin 不会提前关闭。
        env = dict(os.environ)
        env["NOVA_EXEC_SERVER_EXIT_ON_STDIN_CLOSE"] = "1"
        proc = subprocess.Popen(
            [
                binary,
                "--listen",
                "ws://127.0.0.1:0",
                "--ws-auth",
                "capability-token",
                "--ws-token-sha256",
                token_sha256,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
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
        """断开全部客户端（SDK 清扫）并终止本地 spawn 的 executor。"""
        await self._env_manager.close_all()
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
