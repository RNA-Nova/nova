"""环境注册表解析——多 executor 的客户端原语（对位 codex environments 体系）。

codex 对位关系：`~/.codex/environments.toml`（exec-server crate 自持解析）
+ `EnvironmentDefault` 解析。我们把注册表词汇合并在同一个
`~/.nova/executor/config.toml`（层栈已定单文件），`[[environments]]` 条目
字段逐一对位 codex `EnvironmentToml`。

选择/切换编排（哪个会话用哪个环境）不归本层——归调用方（对位 codex core
的 environment_selection；nova 里将来归 bundle 扩展）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Awaitable, Callable, Literal

from .config import ExecutorConfig, ExecutorEnvironment
from .errors import ConfigError

if TYPE_CHECKING:
    from .client import ExecutorClient  # 防循环：client.py 依赖本模块的解析件

from .protocol import NetworkPolicyDecision, NetworkPolicyRequestParams

#: 内建本地环境 id（对位 codex LOCAL_ENVIRONMENT_ID）
LOCAL_ENVIRONMENT_ID = "local"


@dataclass(frozen=True)
class ResolvedEnvironment:
    """解析后的环境（transport 构造参数已归位；frozen 值对象）"""

    id: str
    kind: Literal["local", "ws", "stdio"]
    #: kind="ws" 时的 WS URL
    url: str | None = None
    #: kind="stdio" 时的 spawn 命令（SSH 承载：program="ssh"）
    program: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None
    cwd: str | None = None
    #: 连接总时限（秒；None = 不限制）
    connect_timeout_sec: float | None = None


def resolve_environment(
    config: ExecutorConfig, name: str | None = None
) -> ResolvedEnvironment:
    """按名解析环境；`name=None` 走默认解析链（对位 codex
    normalize_default_environment_id + include_local 语义）：

    - `default_environment` 已设 → 按它解析（"none" = 禁用默认，报错）；
    - 未设 → `include_local=True` 时落内建 local，否则报错。
    """
    if name is None:
        default = config.default_environment
        if default is not None and default.strip().lower() == "none":
            raise ConfigError(
                '默认环境已禁用（default_environment = "none"）——请显式指定'
            )
        name = default or LOCAL_ENVIRONMENT_ID
        if default is None and not config.include_local:
            raise ConfigError("未配置默认环境且 include_local=false——请显式指定")

    if name == LOCAL_ENVIRONMENT_ID:
        if not config.include_local:
            raise ConfigError("内建 local 环境已被 include_local=false 禁用")
        return ResolvedEnvironment(id=LOCAL_ENVIRONMENT_ID, kind="local")

    for environment in config.environments:
        if environment.id == name:
            return _to_resolved(environment)
    available = [e.id for e in config.environments]
    raise ConfigError(
        f"未知环境 `{name}`（已注册：{', '.join(available) or '（空）'}；"
        f"内建：{LOCAL_ENVIRONMENT_ID if config.include_local else '（已禁用）'}）"
    )


def _to_resolved(environment: ExecutorEnvironment) -> ResolvedEnvironment:
    if environment.url is not None:
        return ResolvedEnvironment(
            id=environment.id,
            kind="ws",
            url=environment.url.strip(),
            connect_timeout_sec=environment.connect_timeout_sec,
        )
    assert environment.program is not None  # load 期已校验 url/program 二选一
    return ResolvedEnvironment(
        id=environment.id,
        kind="stdio",
        program=environment.program.strip(),
        args=tuple(environment.args),
        env=dict(environment.env),
        cwd=environment.cwd,
        connect_timeout_sec=environment.connect_timeout_sec,
    )


# =============================================================================
# 多环境连接管理器（对位 codex EnvironmentManager 的连接管理面）
# =============================================================================


class EnvironmentConnectionState(str, Enum):
    """单个环境的连接状态（对位 codex EnvironmentConnectionState + Pending——
    Pending 对位 codex EnvironmentObservedStatus::Pending）"""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    #: 从未连接过 / 正在恢复（观察不触发连接或重连——对位 codex 纪律）
    PENDING = "pending"


@dataclass(frozen=True)
class EnvironmentStatus:
    """环境状态快照（frozen 值对象）"""

    id: str
    state: EnvironmentConnectionState
    #: disconnected 时的最近失败原因（无则 None）
    error: str | None = None


class EnvironmentManager:
    """多 executor 环境管理器（对位 codex `EnvironmentManager`）。

    职责：注册表（ExecutorConfig 的 `[[environments]]` + include_local）+
    按环境 id 懒创建/缓存 `ExecutorClient` + 状态观察 + 生命周期清扫。

    不归它：选择/切换编排（哪个会话用哪个环境）与配置写回（增删端点持久化）
    ——都归调用方。codex 版里的 capability roots 等 agent 概念已剔（executor
    边界纪律）。
    """

    def __init__(
        self,
        config: ExecutorConfig,
        *,
        network_policy: (
            Callable[[NetworkPolicyRequestParams], Awaitable[NetworkPolicyDecision]]
            | None
        ) = None,
    ) -> None:
        self._config = config
        self._network_policy = network_policy
        self._clients: dict[str, ExecutorClient] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 注册表视图
    # ------------------------------------------------------------------

    def environment_ids(self) -> list[str]:
        """全部可用环境 id（注册表 + include_local 时的内建 local）"""
        ids = [e.id for e in self._config.environments]
        if self._config.include_local:
            ids.append(LOCAL_ENVIRONMENT_ID)
        return ids

    @property
    def default_environment_id(self) -> str | None:
        """默认环境 id（解析失败/禁用 → None；对位 codex default_environment()）"""
        try:
            return resolve_environment(self._config).id
        except ConfigError:
            return None

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    async def get_client(self, name: str | None = None) -> ExecutorClient:
        """按名取已连接客户端（懒创建 + 缓存；name=None 走默认解析链）"""
        from .client import ExecutorClient  # 延迟导入破环（client 依赖本模块）

        environment = resolve_environment(self._config, name)
        async with self._lock:
            cached = self._clients.get(environment.id)
            if cached is not None:
                return cached
            client = ExecutorClient.from_environment(
                environment, network_policy=self._network_policy
            )
            await client.connect()
            self._clients[environment.id] = client
            return client

    def status(self, name: str) -> EnvironmentStatus:
        """非变异状态观察（不触发连接/重连——对位 codex EnvironmentObservedStatus）"""
        client = self._clients.get(name)
        if client is None:
            return EnvironmentStatus(id=name, state=EnvironmentConnectionState.PENDING)
        control = client._control  # SDK 内部同窗（状态机归 recovery 层）
        if control.is_connected:
            return EnvironmentStatus(
                id=name, state=EnvironmentConnectionState.CONNECTED
            )
        if control.state == "recovering":
            return EnvironmentStatus(id=name, state=EnvironmentConnectionState.PENDING)
        return EnvironmentStatus(
            id=name,
            state=EnvironmentConnectionState.DISCONNECTED,
            error=control.failure_message,
        )

    # ------------------------------------------------------------------
    # 运行时增删（内存态；配置写回归调用方）
    # ------------------------------------------------------------------

    async def upsert_environment(self, environment: ExecutorEnvironment) -> None:
        """运行时注册/覆盖环境（已有活连接则断开缓存连接，下次用时重建）"""
        existing = self._clients.pop(environment.id, None)
        if existing is not None:
            await existing.disconnect()
        environments = [e for e in self._config.environments if e.id != environment.id]
        environments.append(environment)
        self._config = self._config.model_copy(update={"environments": environments})

    async def remove_environment(self, name: str) -> bool:
        """移除环境（断开并丢弃缓存连接）；返回是否真的存在过"""
        client = self._clients.pop(name, None)
        environments = [e for e in self._config.environments if e.id != name]
        existed = client is not None or len(environments) != len(
            self._config.environments
        )
        if client is not None:
            await client.disconnect()
        self._config = self._config.model_copy(update={"environments": environments})
        return existed

    async def close_all(self) -> None:
        """断开全部缓存连接并清空（进程退出/会话终结时调用）"""
        async with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            try:
                await client.disconnect()
            except Exception:  # 清扫期不因单个连接失败中断其余
                pass
