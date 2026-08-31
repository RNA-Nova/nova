"""执行后端模式格（当前生效后端的运行时真值）。

纪律（设计定案 R2/R3）：
- 本会话格是**执行侧唯一事实源**——bash 引擎与六个 fs 工具执行期直读；
- ``/executor`` 扩展切换时翻转本格（session 条目管记忆、notice 管回执，
  本格管执行）；
- 进程级状态即会话级（后端进程是一会话一进程）。

默认解析：未显式切换过时读 settings ``executor.default_backend``
（``None`` = local）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

from nova_harness.types.config.settings import ExecutorSettings

from nova_coding_agent.executor.policy import SpawnPolicy, resolve_spawn_policy
from nova_coding_agent.tools_common.path_utils import normalize_input, resolve_path


@dataclass
class BackendSelection:
    """当前生效的执行后端选择。"""

    backend: str  # "local" | "executor"
    url: Optional[str] = None  # executor 远程地址（None = 本地回环 spawn）
    # SSH 远程的执行 cwd（远程文件系统与本地无关，不能用本地 cwd）
    remote_cwd: Optional[str] = None
    # SSH 远程家目录（~ 展开用）
    remote_home: Optional[str] = None
    # 随 process/start 下发的沙箱/网络策略（策略归 Nova 设置，执行归 executor）
    spawn_policy: Optional[SpawnPolicy] = None


# 当前生效选择（进程级——后端进程即会话作用域）
_current: Optional[BackendSelection] = None


def get_backend_selection(
    settings: Optional[ExecutorSettings] = None,
) -> BackendSelection:
    """读取当前生效后端（未显式切换过时按 settings 默认）。

    settings 带沙箱档位且默认后端为 executor 时，按本地 cwd 组装策略
    （本地回环 executor 的执行目录就是本机 cwd）。
    """
    global _current
    if _current is not None:
        return _current
    default = (settings.default_backend if settings else None) or "local"
    if default == "executor":
        return BackendSelection(
            backend=default,
            spawn_policy=resolve_spawn_policy(settings, os.getcwd()),
        )
    return BackendSelection(backend=default)


def set_backend_selection(selection: BackendSelection) -> None:
    """翻转当前生效后端（/executor 扩展的写入通道）。"""
    global _current
    _current = selection


def reset_backend_selection() -> None:
    """清空当前选择（测试隔离用）。"""
    global _current
    _current = None


def executor_settings_of(context: Any) -> Optional[ExecutorSettings]:
    """从 ToolContext 形态的 settings 视图读 ExecutorSettings（宽松回退）。"""
    settings = getattr(context, "settings", None)
    getter = getattr(settings, "get_executor_settings", None)
    return getter() if callable(getter) else None


def backend_file_layer(context: Any):
    """当前为远程 executor 后端时返回其 fs 层，否则 None。

    六个 fs 工具执行期解析用：远程（url 非空）→ ``ExecutorFileSystemLayer``
    （按 url 缓存复用）；本地/本地沙箱 → None（继续用本地 layer——本地
    沙箱跑的就是本机盘，fs 绕 WS 无意义）。
    """
    selection = get_backend_selection(executor_settings_of(context))
    if selection.backend != "executor" or not selection.url:
        return None
    from nova_coding_agent.executor.fs_layer import get_executor_file_layer
    from nova_coding_agent.executor.manager import get_executor_manager

    return get_executor_file_layer(get_executor_manager(), selection.url)


def backend_process_runner(context: Any):
    """当前为远程 executor 后端时返回其 ProcessRunner，否则 None。

    grep/find 工具执行期解析用：远程 → ``ExecutorProcessRunner``（远程
    rg 经 process/start，rg 路径随供给探测）；本地/本地沙箱 → None
    （工具缺省构造已带本机 runner）。
    """
    selection = get_backend_selection(executor_settings_of(context))
    if selection.backend != "executor" or not selection.url:
        return None
    from nova_coding_agent.executor.manager import get_executor_manager
    from nova_coding_agent.executor.process_runner import ExecutorProcessRunner

    return ExecutorProcessRunner(
        get_executor_manager(), selection.url, policy=selection.spawn_policy
    )


def resolve_backend_path(path: str, context: Any) -> str:
    """按当前执行后端解析工具路径。

    - 本地：``resolve_path``（本地 cwd 为根、~ 本地展开、macOS 文件名
      变体重试、存在性检查）；
    - 远程 executor：posix 语义——相对路径以 ``remote_cwd`` 为根、
      ``~`` 以 ``remote_home`` 展开、绝对路径原样归一；不查存在性、
      不做 macOS 变体（那是本地文件系统语义，远程不适用）。
    """
    selection = get_backend_selection(executor_settings_of(context))
    if selection.backend == "executor" and selection.url:
        return _resolve_remote_path(path, selection)
    return resolve_path(path, getattr(context, "cwd", None))


def _resolve_remote_path(path: str, selection: BackendSelection) -> str:
    path = normalize_input(path)  # Unicode 空格/@ 剥离（输入净化与后端无关）
    if not path:
        return path
    if path.startswith("~"):
        home = selection.remote_home or "/"
        path = home.rstrip("/") + path[1:]
    if path.startswith("/"):
        return os.path.normpath(path)
    base = selection.remote_cwd or selection.remote_home or "/"
    return os.path.normpath(os.path.join(base, path))
