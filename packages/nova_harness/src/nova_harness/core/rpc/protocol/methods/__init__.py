"""JSON-RPC 方法处理器按领域拆分。

每个子模块提供 ``register(registry, state)`` 函数，把方法注册到
``MethodRegistry`` 中。``state`` 是共享的服务器状态（runtime、ui_context）。
"""

from nova_harness.core.rpc.protocol.methods.auth import (
    register as register_auth_methods,
)
from nova_harness.core.rpc.protocol.methods.model import (
    register as register_model_methods,
)
from nova_harness.core.rpc.protocol.methods.package import (
    register as register_package_methods,
)
from nova_harness.core.rpc.protocol.methods.resources import (
    register as register_resources_methods,
)
from nova_harness.core.rpc.protocol.methods.session import (
    register as register_session_methods,
)
from nova_harness.core.rpc.protocol.methods.settings import (
    register as register_settings_methods,
)
from nova_harness.core.rpc.protocol.methods.system import (
    register as register_system_methods,
)
from nova_harness.core.rpc.protocol.methods.user_tools import (
    register as register_user_tools_methods,
)

__all__ = [
    "register_session_methods",
    "register_user_tools_methods",
    "register_system_methods",
    "register_package_methods",
    "register_model_methods",
    "register_auth_methods",
    "register_resources_methods",
    "register_settings_methods",
]
