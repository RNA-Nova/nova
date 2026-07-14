"""资源加载器配置类型。"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from nova_harness.core.types.extensions import ExtensionFactory
from nova_harness.core.types.protocols import (
    EventBusProtocol,
    ExtensionAPIFactory,
    ModelRegistryProtocol,
    PackageManagerProtocol,
    SettingsManagerProtocol,
)


@dataclass
class DefaultResourceLoaderOptions:
    """资源加载器配置选项（prompt templates + extensions）。

    运行时内部容器，持有 settings_manager / model_registry / event_bus /
    extension_api_factory 等服务实例，因此使用 dataclass 而非 Pydantic。
    """

    cwd: Optional[Union[str, Path]] = field(default_factory=os.getcwd)
    agent_dir: Optional[Union[str, Path]] = None
    settings_manager: Optional[SettingsManagerProtocol] = None
    model_registry: Optional[ModelRegistryProtocol] = None
    additional_prompt_template_paths: Optional[List[Union[str, Path]]] = field(
        default_factory=list
    )
    additional_extension_paths: Optional[List[str]] = field(default_factory=list)
    additional_skill_paths: Optional[List[str]] = field(default_factory=list)
    additional_theme_paths: Optional[List[str]] = field(default_factory=list)
    extension_factories: Optional[List[ExtensionFactory]] = field(default_factory=list)
    no_extensions: bool = False
    no_tools: bool = False
    no_prompt_templates: bool = False
    no_skills: bool = False
    no_themes: bool = True
    no_context_files: bool = False
    event_bus: Optional[EventBusProtocol] = None
    # 用于把 NovaExtensionAPI 的创建推迟到 core 层，避免 resources 向上依赖 core
    extension_api_factory: Optional[ExtensionAPIFactory] = None
    # 运行时资源管理器；ResourceLoader 的发现完全由 package_manager 驱动
    package_manager: Optional[PackageManagerProtocol] = None
    # 当 package_manager 发现 settings 中配置的 package 缺失时是否自动安装
    install_missing_packages: bool = True
    # 项目信任状态；未设置时尝试从 settings_manager 读取
    project_trusted: Optional[bool] = None

    # 资源覆盖回调。每个回调接收加载器准备写入内部状态的原始结果，返回修改后的结果。
    extensions_override: Optional[Callable[[Any], Any]] = None
    skills_override: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    prompts_override: Optional[Callable[[Any], Any]] = None
    agents_override: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None


__all__ = ["DefaultResourceLoaderOptions"]
