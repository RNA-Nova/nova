"""
AgentSession 的 cwd 绑定服务集合。

与 TypeScript 参考实现中的 ``AgentSessionServices`` 对齐：
- 负责创建 cwd 绑定的基础设施（auth/settings/modelRegistry/resourceLoader）。
- 收集创建过程中的 diagnostics（扩展 provider 注册失败等）。
- ``AgentSession`` 与 ``AgentSessionRuntime`` 通过本对象共享依赖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Union

from nova_ai.types.base_model import NovaBaseModel
from pydantic import ConfigDict, Field

from nova_harness.core.agent_session.extensions.api import NovaExtensionAPI
from nova_harness.core.config import AuthStorage, ModelRegistry, SettingsManager
from nova_harness.core.config.defaults import get_agent_dir
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.harness.system_prompt import SystemPromptManager
from nova_harness.core.resources.loader import DefaultResourceLoader, ResourceLoader
from nova_harness.core.types.diagnostics import AgentSessionRuntimeDiagnostic
from nova_harness.core.types.resource import DefaultResourceLoaderOptions

if TYPE_CHECKING:
    from nova_harness.core.agent_session.agent import AgentSession


@dataclass
class CreateAgentSessionRuntimeResult:
    """Runtime 工厂返回的结果。"""

    session: "AgentSession"
    services: "AgentSessionServices"
    diagnostics: List[AgentSessionRuntimeDiagnostic] = field(default_factory=list)
    model_fallback_message: Optional[str] = None


class AgentSessionServices(NovaBaseModel):
    """
    与某个 cwd/session 绑定的服务集合。

    这些服务在 AgentSession 生命周期内相对稳定；
    切换会话时 Runtime 可以选择复用或重新创建 services。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, validate_assignment=True)

    cwd: str
    agent_dir: str
    session_manager: Optional[SessionManager] = None
    settings_manager: SettingsManager
    model_registry: ModelRegistry
    resource_loader: ResourceLoader
    system_prompt_manager: SystemPromptManager
    auth_storage: AuthStorage
    diagnostics: List[AgentSessionRuntimeDiagnostic] = Field(default_factory=list)

    @classmethod
    async def create(
        cls,
        cwd: str,
        agent_dir: Optional[Union[str, Path]] = None,
        session_manager: Optional[SessionManager] = None,
        auth_storage: Optional[AuthStorage] = None,
        settings_manager: Optional[SettingsManager] = None,
        model_registry: Optional[ModelRegistry] = None,
        resource_loader: Optional[ResourceLoader] = None,
        system_prompt_manager: Optional[SystemPromptManager] = None,
        agent_name: Optional[str] = None,
    ) -> "AgentSessionServices":
        """
        创建 cwd 绑定的服务集合。

        返回的 services 已经包含 authStorage、settingsManager、modelRegistry、
        resourceLoader（已 reload）、systemPromptManager。扩展由 ResourceLoader 加载，
        AgentSession 在初始化时从 ResourceLoader 取出扩展并创建 ExtensionRunner。
        """
        resolved_cwd = str(Path(cwd).resolve())
        resolved_agent_dir = (
            str(Path(agent_dir).resolve())
            if agent_dir
            else str(Path(get_agent_dir()).resolve())
        )
        auth_storage = auth_storage or AuthStorage.create(
            Path(resolved_agent_dir) / "auth.json"
        )
        settings_manager = settings_manager or SettingsManager.create(
            resolved_cwd, resolved_agent_dir
        )
        model_registry = model_registry or ModelRegistry(
            auth_storage, Path(resolved_agent_dir) / "models.json"
        )

        if resource_loader is None:
            resource_loader = DefaultResourceLoader(
                DefaultResourceLoaderOptions(
                    cwd=resolved_cwd,
                    agent_dir=resolved_agent_dir,
                    settings_manager=settings_manager,
                    model_registry=model_registry,
                    additional_prompt_template_paths=[],
                    additional_extension_paths=[],
                    no_prompt_templates=False,
                    no_extensions=False,
                    extension_api_factory=lambda extension, context: NovaExtensionAPI(
                        extension, context
                    ),
                )
            )
            await resource_loader.reload()

        if system_prompt_manager is None:
            resolved_agent_name = agent_name
            if not resolved_agent_name:
                names = resource_loader.get_agent_names()
                if names:
                    resolved_agent_name = names[0]
                else:
                    resolved_agent_name = "base_agent"
            system_prompt_manager = SystemPromptManager(
                resource_loader, resolved_agent_name
            )

        return cls(
            cwd=resolved_cwd,
            agent_dir=resolved_agent_dir,
            session_manager=session_manager,
            settings_manager=settings_manager,
            model_registry=model_registry,
            resource_loader=resource_loader,
            system_prompt_manager=system_prompt_manager,
            auth_storage=auth_storage,
            diagnostics=[],
        )
