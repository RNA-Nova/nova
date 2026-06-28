"""
Nova harness 统一类型层。

本包聚合跨模块使用的数据类型与事件 payload，按主题拆分到子模块：
- `agent_config.py`：Agent 配置与系统提示词相关类型
- `messages.py`：消息类型
- `session.py`：会话条目与会话树类型
- `events.py`：事件常量与 payload
- `extensions.py`：扩展注册类型
- `resource.py`：资源加载相关类型
- `model_registry.py`：模型注册表类型
- `setting.py`：设置类型
- `tools.py`：工具定义类型
- ...

不再做大规模的顶层重导出；请按需要从对应子模块导入类型。
"""
