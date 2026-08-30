"""执行策略（SpawnPolicy）：随 ``process/start`` 下发的沙箱/网络策略。

设计纪律（定案：**策略归 Nova 设置，执行归 executor**）：

- 客户端把策略组装成 ``SpawnPolicy``，挂在 ``BackendSelection`` 上随后端
  切换生效；bash 引擎与 process_runner 执行期只做透传；
- executor 收到什么执行什么，不理解 nova 语义（纯执行后端纪律）；
- fs 沙箱档位（read-only / workspace-write）是首批真实生产者——executor
  三平台沙箱与 SDK 模型均全实现；
- ``network_proxy`` 等深层线上结构暂以原样 dict 承载：settings 语义等
  executor 网络沙箱批次一起定，不对 stub 臆造配置格式。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from nova_executor_client.protocol import FileSystemSandboxContext

#: ExecutorSettings.sandbox 支持的档位（None = 不沙箱，现状默认）
SANDBOX_TIERS = ("read-only", "workspace-write")


@dataclass(frozen=True)
class SpawnPolicy:
    """一次 ``process/start`` 的策略载荷（只含显式配置的项）。"""

    #: FileSystemSandboxContext wire 形态（fs 沙箱）
    sandbox: Optional[Dict[str, Any]] = None
    #: RemoteNetworkProxyLaunchConfig wire 形态（托管网络，格式待网络批次定）
    network_proxy: Optional[Dict[str, Any]] = None
    #: 托管网络强制开关
    enforce_managed_network: bool = False
    #: ManagedNetworkSandboxContext wire 形态
    managed_network: Optional[Dict[str, Any]] = None

    def start_kwargs(self) -> Dict[str, Any]:
        """转 ``process/start`` 的额外 kwargs（camel wire 键；None 项不出场）。"""
        kwargs: Dict[str, Any] = {}
        if self.sandbox is not None:
            kwargs["sandbox"] = self.sandbox
        if self.network_proxy is not None:
            kwargs["networkProxy"] = self.network_proxy
        if self.enforce_managed_network:
            kwargs["enforceManagedNetwork"] = True
        if self.managed_network is not None:
            kwargs["managedNetwork"] = self.managed_network
        return kwargs


def resolve_spawn_policy(
    executor_settings: Any, effective_cwd: Optional[str]
) -> Optional[SpawnPolicy]:
    """从 ExecutorSettings 沙箱档位组装策略（无档位/无 cwd → None，不沙箱）。

    ``effective_cwd``：策略作用目录——SSH 远程取 remote_cwd（会话隔离
    工作区），本地回环 executor 取本地 cwd；ws 直连暂无已知的远程 cwd，
    v1 不沙箱（登记限制：等 remote_cwd 语义覆盖 ws 端点再放开）。
    """
    tier = getattr(executor_settings, "sandbox", None) if executor_settings else None
    if tier not in SANDBOX_TIERS or not effective_cwd:
        return None
    if tier == "read-only":
        context = FileSystemSandboxContext.read_only(effective_cwd)
    else:
        context = FileSystemSandboxContext.workspace_write(effective_cwd)
    return SpawnPolicy(sandbox=context.model_dump(by_alias=True, exclude_none=True))
