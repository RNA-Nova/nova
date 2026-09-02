"""资源加载器配置类型。"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from nova_harness.core.types.extensions import ExtensionFactory
from nova_harness.core.types.protocols import (
    EventBusProtocol,
    ExtensionAPIFactory,
    ModelRuntimeProtocol,
    PackageManagerProtocol,
    SettingsManagerProtocol,
)
from nova_harness.core.types.resources.context_files import ContextFile
from nova_harness.core.types.resources.tools import ToolContext


@dataclass
class DefaultResourceLoaderOptions:
    """资源加载器配置选项（prompt templates + extensions）。

    运行时内部容器，持有 settings_manager / model_runtime / event_bus /
    extension_api_factory 等服务实例，因此使用 dataclass 而非 Pydantic。
    """

    cwd: Optional[Union[str, Path]] = field(default_factory=os.getcwd)
    agent_dir: Optional[Union[str, Path]] = None
    settings_manager: Optional[SettingsManagerProtocol] = None
    model_runtime: Optional[ModelRuntimeProtocol] = None
    # 包 LLM 工具的构造期上下文（cwd + settings 活视图，不变量）；
    # 缺省时 loader 以 cwd + 空设置视图自建。执行期的当前模型经
    # execute 第 5 参（ToolExecContext）注入，与本字段无关。
    tool_context: Optional[ToolContext] = None
    # 显式传入的静态资源路径（--skill / --prompt-template CLI 与 SDK 注入
    # 共用此通道，对齐 pi 的单通道设计）：最低优先层，在 resolver 资源之后、
    # 扩展贡献之前加载。
    additional_prompt_template_paths: Optional[List[Union[str, Path]]] = field(
        default_factory=list
    )
    additional_extension_paths: Optional[List[str]] = field(default_factory=list)
    additional_skill_paths: Optional[List[str]] = field(default_factory=list)
    extension_factories: Optional[List[ExtensionFactory]] = field(default_factory=list)
    no_extensions: bool = False
    no_tools: bool = False
    no_prompt_templates: bool = False
    no_skills: bool = False
    no_context_files: bool = False
    event_bus: Optional[EventBusProtocol] = None
    # 用于把 NovaExtensionAPI 的创建推迟到 core 层，避免 resources 向上依赖 core
    extension_api_factory: Optional[ExtensionAPIFactory] = None
    # 运行时资源管理器；ResourceLoader 的发现完全由 package_manager 驱动
    package_manager: Optional[PackageManagerProtocol] = None
    # 当 package_manager 发现 settings 中配置的 package 缺失时是否自动安装
    install_missing_packages: bool = True

    # 资源覆盖回调。每个回调接收加载器准备写入内部状态的原始结果，返回修改后的结果。
    extensions_override: Optional[Callable[[Any], Any]] = None
    skills_override: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    prompts_override: Optional[Callable[[Any], Any]] = None
    agents_override: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    # 上下文文件覆盖回调（对齐 pi 的 agentsFilesOverride）：在 no_context_files
    # 之后应用——SDK 可以过滤、改写，也可以在禁用自动发现后注入自定义条目。
    context_files_override: Optional[
        Callable[[List[ContextFile]], List[ContextFile]]
    ] = None


__all__ = ["DefaultResourceLoaderOptions"]
