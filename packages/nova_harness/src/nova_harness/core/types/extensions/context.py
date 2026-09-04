"""扩展 handler 上下文类型。

用于替代 ``SimpleNamespace``，提供更强的类型提示与运行时安全性。

纪律：所有字段**构造必选**（kw_only、无默认）——新增字段若漏在
``ExtensionRunner.create_context`` 接线，构造即报错（响亮失败），
而不是 handler 调用时静默 no-op。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from nova_harness.core.types.protocols import (
    ModelRuntimeProtocol,
    SessionManagerProtocol,
)
from nova_harness.core.types.ui import UIContext


@dataclass(kw_only=True)
class ExtensionContext:
    """扩展事件 handler 使用的上下文对象（全字段必选）。"""

    # 核心 action（由 ExtensionRuntime.actions 代理）
    send_message: Callable[..., Any]
    send_user_message: Callable[..., Any]
    exec: Callable[..., Any]
    append_entry: Callable[..., Any]
    set_session_name: Callable[..., Any]
    get_session_name: Callable[..., Any]
    set_label: Callable[..., Any]
    get_active_tools: Callable[..., Any]
    get_all_tools: Callable[..., Any]
    set_active_tools: Callable[..., Any]
    refresh_tools: Callable[..., Any]
    get_commands: Callable[..., Any]
    set_model: Callable[..., Any]
    get_thinking_level: Callable[..., Any]
    set_thinking_level: Callable[..., Any]

    # 上下文 action（由 ExtensionRuntime.context_actions 代理）
    is_idle: Callable[..., Any]
    is_project_trusted: Callable[..., Any]
    get_signal: Callable[..., Any]
    abort: Callable[..., Any]
    has_pending_messages: Callable[..., Any]
    shutdown: Callable[..., Any]
    get_context_usage: Callable[..., Any]
    compact: Callable[..., Any]
    get_system_prompt: Callable[..., Any]
    get_system_prompt_options: Callable[..., Any]
    # persona 旋钮（由 ExtensionRuntime.context_actions 代理）
    get_personas: Callable[..., Any]
    get_persona_override: Callable[..., Any]
    set_persona_override: Callable[..., Any]
    clear_persona_override: Callable[..., Any]
    # agent 旋钮（由 ExtensionRuntime.context_actions 代理）
    get_agents: Callable[..., Any]
    change_agent: Callable[..., Any]
    save_agent: Callable[..., Any]
    # 请求重建系统提示词（环境段内容变化后——权限档位变化等）
    refresh_system_prompt: Callable[..., Any]

    # 环境信息
    ui: UIContext
    has_ui: bool
    cwd: str
    extension_path: Optional[str]

    # Session / model 访问（与 TS 对齐）
    session_manager: Optional[SessionManagerProtocol]
    model_runtime: Optional[ModelRuntimeProtocol]
    _get_model: Callable[..., Any]

    @property
    def model(self) -> Any:
        """每次访问都返回当前模型，避免创建 context 时快照导致过期。"""
        return self._get_model()

    assert_active: Callable[[], None]


@dataclass(kw_only=True)
class ExtensionCommandContext(ExtensionContext):
    """扩展命令 handler 使用的上下文对象。

    在 ``ExtensionContext`` 基础上增加会话控制 action。
    """

    wait_for_idle: Callable[..., Any]
    new_session: Callable[..., Any]
    fork: Callable[..., Any]
    navigate_tree: Callable[..., Any]
    switch_session: Callable[..., Any]
    reload: Callable[..., Any]
    get_session_info: Callable[..., Any]
    """当前 scoped 模型池（List[ScopedModelConfig]——/scoped-models headless 回退等）。"""
    get_scoped_models: Callable[..., Any]
    trust_project: Callable[..., Any]
    untrust_project: Callable[..., Any]
    clone: Callable[..., Any]
    export: Callable[..., Any]
    import_session: Callable[..., Any]


__all__ = ["ExtensionContext", "ExtensionCommandContext"]
