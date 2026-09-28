"""执行策略载荷（SpawnPolicy）：随 ``process/start`` 下发的沙箱/网络策略。

物化已升级：沙箱物化走 nova_protocol 的 ExecutorConfig.resolve_execution
（本模块只剩载荷容器与 wire 转换——档位/套餐解析在 nova_protocol 引擎）。

设计纪律（定案：**策略归 Nova 设置，执行归 executor**）：

- 客户端把物化后的策略装进 ``SpawnPolicy``，挂在 ``BackendSelection`` 上
  随后端切换生效；bash 引擎与 process_runner 执行期只做透传；
- executor 收到什么执行什么，不理解 nova 语义（纯执行后端纪律）；
- ``network_proxy`` 等深层线上结构暂以原样 dict 承载：语义等 executor
  网络沙箱批次一起定，不对 stub 臆造配置格式。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class SpawnPolicy:
    """一次 ``process/start`` 的策略载荷（只含显式配置的项）。"""

    #: FileSystemSandboxContext wire 形态（fs 沙箱——由
    #: ExecutorConfig.resolve_execution 物化产出）
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
