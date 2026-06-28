# ============================================================================
# 配置类定义
# ============================================================================
from __future__ import annotations

from typing import Any, Dict, List, Optional

from nova_agent import Agent, AgentTool
from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field, model_validator

from nova_harness.core.agent_session.services import AgentSessionServices
from nova_harness.core.config import ModelRegistry, SettingsManager
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.harness.system_prompt import SystemPromptManager
from nova_harness.core.resources.loader import ResourceLoader
from nova_harness.core.types.agent import ScopedModelConfig
from nova_harness.core.types.events import SessionStartEvent


class AgentSessionConfig(NovaBaseModel):
    """
    AgentSession 的完整配置。

    与 TypeScript 参考实现保持一致：config 是扁平的字段集合，
    AgentSession 直接持有这些依赖；AgentSessionRuntime 通过
    AgentSessionServices 统一管理服务，并在创建 session 时解包成此 config。

    扩展系统由 AgentSession 在初始化时自行构建：它从 ``resource_loader``
    读取已加载的扩展，创建 ``ExtensionRunner``，并通过可选的
    ``extension_runner_ref`` 把 runner 引用暴露给外部（如 SDK）。
    """

    model_config = NovaBaseModel.model_config.copy()
    model_config["arbitrary_types_allowed"] = True

    agent: "Agent"
    session_manager: "SessionManager"
    settings_manager: "SettingsManager"
    cwd: str
    system_prompt_manager: "SystemPromptManager"
    resource_loader: "ResourceLoader"
    model_registry: "ModelRegistry"
    scoped_models: List[ScopedModelConfig] = Field(default_factory=list)
    initial_active_tool_names: List[str] = Field(
        default_factory=lambda: ["read", "bash", "edit", "write"]
    )
    base_tools_override: Optional[Dict[str, "AgentTool"]] = None
    # 可选：AgentSession 内部创建 runner 后写回此 ref，供外部获取当前 runner
    extension_runner_ref: Optional[Dict[str, Optional[Any]]] = Field(
        default=None, exclude=True
    )
    # AgentSession 内部创建 runner 所需的服务集合
    services: Optional["AgentSessionServices"] = Field(default=None, exclude=True)
    # 会话启动事件；由 AgentSession 在 runner 就绪后 emit
    session_start_event: Optional[SessionStartEvent] = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def validate_required(self):
        if not self.cwd:
            raise ValueError("cwd (current working directory) cannot be empty")
        if self.agent is None:
            raise ValueError("agent cannot be None")
        return self


__all__ = ["AgentSessionConfig"]
