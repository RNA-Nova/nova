"""兼容别名（过渡件）——本包已更名 `nova_exec_server_client`。

`from nova_executor_client import X` 在迁移期内继续可用；新代码请直接用
`nova_exec_server_client`。R3 消费方全部迁完后本目录删除。
"""

import sys as _sys

import nova_exec_server_client as _pkg
from nova_exec_server_client import *  # noqa: F401,F403

# 让 `import nova_executor_client.<submodule>` 解析到新包
_sys.modules[__name__] = _pkg
