"""Nova harness 统一类型层。

本包聚合跨模块使用的数据类型与事件 payload，按主题分组：

- `compaction`：上下文压缩相关类型
- `config`：设置、快捷键等配置类型
- `events`：事件类型、结果类型与事件名字符串常量
- `extensions`：扩展协议类型（含 bash spawn hook 契约，统一从 `extensions` 导入）
- `messages`：消息类型与摘要前缀/后缀常量
- `model`：模型注册表运行时类型
- `package`：包管理域类型
- `project_trust`：Project Trust 决策类型
- `resources`：资源类型（agents / skills / prompts / tools / context files / 诊断）
- `session`：AgentSession 生命周期、会话条目、会话树、模型配置与运行时诊断
- `ui`：UI 能力抽象类型（`UIContext` / `UIResponse` / 空实现 `NoOpUIContext`）

设计约定
----------
1. 每个子包/模块的 `__init__.py` 完整导出该子包的公共类型与常量，调用方统一从
   子包导入，例如：
   ```python
   from nova_harness.core.types.session import AgentSessionConfig
   from nova_harness.core.types.events.constants import TOOL_CALL
   ```
2. 常量统一收敛到各子包的 `constants.py`：
   - `events.constants`：所有事件名字符串常量
   - `session.constants`：`CURRENT_SESSION_VERSION`
   - `messages`：摘要前缀/后缀常量（模块较小，常量与类型同文件）
3. 事件名字符串应通过 `events.constants` 中的常量引用，避免直接写硬编码字符串。
4. 类型层内部的跨子包引用使用完整子模块路径（`types.resources.skills`），
   不走包级 re-export，避免 `__init__` 链形成循环 import。
"""

from nova_harness.core.types import (
    compaction,
    config,
    events,
    extensions,
    messages,
    model,
    package,
    project_trust,
    resources,
    session,
    ui,
)

__all__ = [
    "compaction",
    "config",
    "events",
    "extensions",
    "messages",
    "model",
    "package",
    "project_trust",
    "resources",
    "session",
    "ui",
]
