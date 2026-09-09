"""RuntimeManager — 进程级唯一会话工厂 + 注册表。

对外只导出 manager 侧符号；组装编排见 ``assembly``（由 ``sdk.py``
薄壳与 ``manager`` 调用），不在此 re-export——保持导入图无环与
入口顺序无关。

**导入无环不变量**（冷导入测试 ``test_imports.py`` 守门）：
``manager`` 模块级不 import ``assembly``（默认工厂在函数体内懒加载）；
``assembly`` 及整条组装链不得反向 import ``nova_harness.core``。
harness 层无 sdk 模块——协议嵌入走 nova_server 的 ``client/in_process.py``。
"""

from nova_harness.core.runtime_manager.manager import (
    RuntimeManager,
    SessionIdCollisionError,
    SessionShutdownReport,
)

__all__ = [
    "RuntimeManager",
    "SessionIdCollisionError",
    "SessionShutdownReport",
]
