"""nova-executor 接入模块（nova_coding_agent bundle）。

- ``manager``：executor 客户端生命周期（本地 spawn / 远程连接 / SSH 隧道）；
- ``backend``：``ExecutorBashOperations``（BashOperations 协议的 executor 实现）；
- ``runtime``：当前生效后端的模式格（/executor 扩展翻转、bash 引擎直读）；
- ``policy``：执行策略（SpawnPolicy——沙箱/网络策略组装与解析）；
- ``provision``：SSH 远程供给器（密钥引导 / 二进制上传 / 回环隧道）。
"""

from nova_coding_agent.executor.backend import ExecutorBashOperations
from nova_coding_agent.executor.manager import (
    ExecutorClientManager,
    get_executor_manager,
    resolve_executor_binary,
)
from nova_coding_agent.executor.policy import (
    SpawnPolicy,
    resolve_spawn_policy,
)
from nova_coding_agent.executor.provision import (
    ProvisionError,
    SshTarget,
    is_ssh_url,
    parse_ssh_target,
)
from nova_coding_agent.executor.runtime import (
    BackendSelection,
    backend_file_layer,
    backend_process_runner,
    get_backend_selection,
    reset_backend_selection,
    resolve_backend_path,
    set_backend_selection,
)

__all__ = [
    "BackendSelection",
    "ExecutorBashOperations",
    "ExecutorClientManager",
    "ProvisionError",
    "SpawnPolicy",
    "SshTarget",
    "backend_file_layer",
    "backend_process_runner",
    "get_backend_selection",
    "get_executor_manager",
    "is_ssh_url",
    "parse_ssh_target",
    "reset_backend_selection",
    "resolve_backend_path",
    "resolve_executor_binary",
    "resolve_spawn_policy",
    "set_backend_selection",
]
