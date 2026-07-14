"""Nova harness 统一类型层。

本包聚合跨模块使用的数据类型与事件 payload，按主题分组：

- `agent`：Agent 定义相关类型
- `compaction`：上下文压缩相关类型
- `config`：鉴权、模型注册表、设置类型
- `events`：事件类型、结果类型与事件名字符串常量
- `extensions`：扩展协议类型（已按子主题拆分模块，统一从 `extensions` 导入）
- `keybindings`：快捷键配置与冲突诊断类型
- `messages`：消息类型与摘要前缀/后缀常量
- `package_manager`：包管理器类型
- `project_trust`：Project Trust 决策类型
- `resources`：资源加载相关类型
- `runtime`：运行时执行对象类型（Bash、工具、运行时诊断）
- `session`：AgentSession 生命周期、会话条目、会话树类型与常量
- `skills`：Skill 相关类型
- `ui`：UI 能力抽象类型（`UIContext` / `UIResponse`）；空实现位于 `core.ui.noop`

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
"""

from nova_harness.core.types import (
    agent,
    compaction,
    config,
    events,
    extensions,
    keybindings,
    messages,
    package_manager,
    project_trust,
    resources,
    runtime,
    session,
    skills,
    ui,
)

__all__ = [
    "agent",
    "compaction",
    "config",
    "events",
    "extensions",
    "keybindings",
    "messages",
    "package_manager",
    "project_trust",
    "resources",
    "runtime",
    "session",
    "skills",
    "ui",
]
