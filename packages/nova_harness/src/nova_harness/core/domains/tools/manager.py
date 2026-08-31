"""ToolsManager — 统一管理工具发现、过滤、激活与元数据。

把原本分散在 ``ToolController`` 和 ``SystemPromptManager`` 中的工具管理逻辑
集中到这里，成为 AgentSession 内部工具相关能力的单一数据源。

definition-first：三个来源（包 / SDK custom / override）统一归约为
``ToolDefinition``，``refresh`` 时用 ``context_provider`` 统一包装为
``DynamicTool``（对齐 pi ``_refreshToolRegistry`` 的"加载层定义、
会话层包装"）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from nova_agent import AgentTool

from nova_harness.core.harness.tools.dynamic_tool import (
    DynamicTool,
    create_tool_definition_from_agent_tool,
)
from nova_harness.core.types.protocols import (
    ExtensionRunnerProtocol,
    ResourceLoaderProtocol,
)
from nova_harness.core.types.resources.selection import CapabilitySelection
from nova_harness.core.types.resources.tools import (
    ToolContextProvider,
    ToolDefinition,
    ToolInfo,
)
from nova_harness.core.utils.name_sets import (
    apply_name_list,
    build_selection_report,
)


def _definition_source_info(
    definition: Optional[ToolDefinition],
) -> Dict[str, Optional[str]]:
    """从 ToolDefinition 提取来源信息。"""
    if definition is None:
        return {"source": None, "source_path": None}
    if definition.source_info is not None:
        return {
            "source": definition.source_info.source,
            "source_path": definition.source_info.path,
        }
    if definition.tool_dir:
        return {"source": "package", "source_path": definition.tool_dir}
    if definition.executor_path:
        return {"source": "package", "source_path": definition.executor_path}
    return {"source": None, "source_path": None}


@dataclass
class ToolsManager:
    """
    工具管理中心。

    职责：
    1. 发现候选工具：包管理工具 + 扩展工具 + 调用方覆盖
    2. 名单裁决（单点）：settings pattern（用户终裁）→ SDK allow/exclude
       （宿主硬闸）→ agent.yaml 名单（角色选配，``role_boundary=strict``
       时入注册表裁决，``open`` 时只做初始激活集）——词汇与三态归
       ``core/utils/name_sets.py``
    3. 维护实际 ``AgentTool`` 注册表与 ``ToolDefinition`` 元数据
    4. 决定并暴露当前激活工具集合
    5. 产出 CapabilitySelection 报告（yaml 点名项的成功/失败与原因）
    """

    resource_loader: ResourceLoaderProtocol
    extension_runner: Optional[ExtensionRunnerProtocol] = None
    base_tools_override: Optional[Dict[str, AgentTool]] = None
    custom_tools: Optional[List[ToolDefinition]] = None
    allowed_tool_names: Optional[Set[str]] = None
    excluded_tool_names: Optional[Set[str]] = None
    # settings ``tools`` 键（名字 pattern，用户终裁层）——由会话注入
    settings_tool_patterns: Optional[List[str]] = None
    # open（默认）：yaml 名单 = 初始激活集；strict：yaml 名单 = 注册表闸门
    role_boundary: str = "open"
    agent_name: Optional[str] = None

    _tool_registry: Dict[str, AgentTool] = field(default_factory=dict, init=False)
    _tool_definitions: Dict[str, ToolDefinition] = field(
        default_factory=dict, init=False
    )
    _active_tools: List[str] = field(default_factory=list, init=False)
    _selection_report: List[CapabilitySelection] = field(
        default_factory=list, init=False
    )

    def __post_init__(self) -> None:
        if self.allowed_tool_names is not None:
            self.allowed_tool_names = set(self.allowed_tool_names)
        if self.excluded_tool_names is not None:
            self.excluded_tool_names = set(self.excluded_tool_names)

    # -------------------------------------------------------------------------
    # Discovery & filtering
    # -------------------------------------------------------------------------

    def _discover_candidate_definitions(self) -> Dict[str, ToolDefinition]:
        """发现所有候选工具 definition（未过滤、未包装）。"""
        candidates: Dict[str, ToolDefinition] = {}

        # 1) 包管理器安装的工具（loader 已产出 definition）
        package_tools = self.resource_loader.get_tools().get("tools", {})
        if package_tools:
            candidates.update(package_tools)

        # 2) custom tools（由 SDK/调用方直接传入的 ToolDefinition）
        if self.custom_tools:
            for definition in self.custom_tools:
                candidates[definition.name] = definition

        # 3) 调用方显式覆盖最高优先级（纯 AgentTool 合成 definition）
        if self.base_tools_override:
            for name, tool in self.base_tools_override.items():
                candidates[name] = create_tool_definition_from_agent_tool(tool)

        return candidates

    def _agent_tool_list(self) -> Optional[List[str]]:
        """当前 agent yaml 的 tools 名单（原始条目，含 ``!`` 前缀；三态）。"""
        config = self._agent_config()
        if config is None or config.tools is None:
            return None
        return [t.name for t in config.tools]

    def _agent_config(self) -> Optional[Any]:
        """尝试从 ResourceLoader 获取当前 agent 配置。"""
        if not hasattr(self.resource_loader, "get_agents"):
            return None
        agents = self.resource_loader.get_agents()
        # MagicMock 可能真值判断为 True，因此显式检查是否为非空 dict
        if not isinstance(agents, dict) or not agents:
            return None
        # 优先使用当前 agent 名称；未指定时回退到第一个 agent
        if self.agent_name is not None:
            return agents.get(self.agent_name)
        return next(iter(agents.values()))

    def _resolve_registry_names(
        self, candidates: Dict[str, ToolDefinition]
    ) -> Set[str]:
        """注册表裁决单点：settings → SDK；strict 模式下再叠 yaml 名单。

        各层只收窄（求交），层序无关；SDK 是宿主硬闸（任何模式下都硬）。
        """
        registry = apply_name_list(candidates.keys(), self.settings_tool_patterns)
        if self.allowed_tool_names is not None:
            registry &= self.allowed_tool_names
        if self.excluded_tool_names is not None:
            registry -= self.excluded_tool_names
        if self.role_boundary == "strict":
            registry = apply_name_list(registry, self._agent_tool_list())
        return registry

    def _default_active_names(self, registry: Dict[str, AgentTool]) -> List[str]:
        """默认激活集：open 模式 = yaml 名单作用于注册表（None=全激活）。

        保序：按注册表（候选发现）顺序输出。
        """
        allowed = apply_name_list(registry.keys(), self._agent_tool_list())
        return [n for n in registry if n in allowed]

    def _build_selection_report(
        self,
        candidates: Dict[str, ToolDefinition],
        registry: Set[str],
    ) -> List[CapabilitySelection]:
        """产出 yaml 点名项的裁决报告（判定逻辑归 ``name_sets`` 共享函数）。

        tools 域是信息最全的裁决点：settings（名字 pattern）与 SDK
        allow/exclude 两层存活集都可知，逐层归因。
        """
        after_settings = apply_name_list(candidates.keys(), self.settings_tool_patterns)
        after_sdk = set(after_settings)
        if self.allowed_tool_names is not None:
            after_sdk &= self.allowed_tool_names
        if self.excluded_tool_names is not None:
            after_sdk -= self.excluded_tool_names
        return build_selection_report(
            "tools",
            self._agent_tool_list(),
            candidates.keys(),
            surviving_after_settings=after_settings,
            surviving_after_sdk=after_sdk,
            final=registry,
        )

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def refresh(
        self,
        active_tool_names: Optional[List[str]] = None,
        context_provider: Optional[ToolContextProvider] = None,
    ) -> None:
        """重建工具注册表、元数据与激活集合。

        ``context_provider`` 随包装注入每个 ``DynamicTool``（对齐 pi
        ``wrapRegisteredTools(tools, runner)`` 的 refresh 时包装）；缺省时
        工具拿到 ``NULL_TOOL_EXEC_CONTEXT``（standalone / 测试场景）。
        """
        candidates = self._discover_candidate_definitions()
        registry_names = self._resolve_registry_names(candidates)
        self._selection_report = self._build_selection_report(
            candidates, registry_names
        )

        # 应用过滤并统一包装
        registry: Dict[str, AgentTool] = {}
        definitions: Dict[str, ToolDefinition] = {}
        for name, definition in candidates.items():
            if name not in registry_names:
                continue
            definitions[name] = definition
            registry[name] = DynamicTool(definition, context_provider)

        self._tool_registry = registry
        self._tool_definitions = definitions
        self._inject_spawn_hooks(definitions)

        # 决定激活工具
        self._decide_active_tools(
            active_tool_names=active_tool_names,
        )

    # -------------------------------------------------------------------------
    # Spawn hook 注入
    # -------------------------------------------------------------------------

    def _inject_spawn_hooks(self, definitions: Dict[str, ToolDefinition]) -> None:
        """把扩展注册的 bash spawn hooks 注入支持 hook 的工具执行体。

        与会话 bash 等 spawn 类工具共用同一批扩展 hook（``registerSpawnHook``）：
        LLM 工具链（模型的 bash 调用）和用户通道执行环境一致。
        hook 为 None 时显式清除，保证 reload 后不留陈旧 hook。
        """
        hook = self._extension_spawn_hook()
        for definition in definitions.values():
            executor = getattr(definition.execute, "__self__", None)
            setter = getattr(executor, "set_spawn_hook", None)
            if callable(setter):
                setter(hook)

    def _extension_spawn_hook(self) -> Optional[Any]:
        """把扩展注册的 spawn hooks 聚合为一个惰性 hook（调用时读最新列表）。"""
        runner = self.extension_runner
        if runner is None:
            return None

        def _hook(ctx: Any) -> Any:
            current = ctx
            for h in getattr(runner.runtime, "spawn_hooks", None) or []:
                current = h(current)
            return current

        return _hook

    def _decide_active_tools(
        self,
        active_tool_names: Optional[List[str]] = None,
    ) -> None:
        """根据注册表和调用方偏好决定激活工具。

        框架零内置工具、零预设名单：默认激活 = yaml 名单作用于注册表
        （open 模式）；未声明（None）时注册表全部激活。
        """
        registry = self._tool_registry

        # 若调用方显式指定，优先使用（空列表表示显式不激活）
        if active_tool_names is not None:
            self._active_tools = [n for n in active_tool_names if n in registry]
            return

        self._active_tools = list(self._default_active_names(self._tool_registry))

    @property
    def selection_report(self) -> List[CapabilitySelection]:
        """最近一次 refresh 的 yaml 选配报告。"""
        return list(self._selection_report)

    def set_active_tools(self, tool_names: List[str]) -> None:
        """按名称设置激活工具（自动过滤掉不在注册表中的名称）。"""
        self._active_tools = [n for n in tool_names if n in self._tool_registry]

    def get_active_tools(self) -> List[str]:
        """返回当前激活工具名称列表。"""
        return list(self._active_tools)

    def get_available_tools(self) -> List[str]:
        """返回注册表中所有可用工具名称列表。"""
        return list(self._tool_registry.keys())

    def get_tool(self, name: str) -> Optional[AgentTool]:
        """按名称返回工具实例。"""
        return self._tool_registry.get(name)

    def get_tool_definition(self, name: str) -> Optional[ToolDefinition]:
        """按名称返回工具定义。"""
        return self._tool_definitions.get(name)

    def get_all_tools(self) -> List[ToolInfo]:
        """返回所有可用工具的完整 ``ToolInfo`` 列表。"""
        tools: List[ToolInfo] = []
        seen: Set[str] = set()
        for name, tool in self._tool_registry.items():
            if name in seen:
                continue
            seen.add(name)

            # 注册表 definition-first：合成 definition 保证恒存在
            definition = self._tool_definitions.get(name)
            source_info = _definition_source_info(definition)

            tools.append(
                ToolInfo(
                    name=name,
                    description=definition.description if definition else "",
                    parameters=(
                        definition.parameters
                        if definition
                        else getattr(tool, "parameters", None)
                    ),
                    prompt_snippet=definition.prompt_snippet if definition else None,
                    prompt_guidelines=(
                        definition.prompt_guidelines if definition else None
                    ),
                    source=source_info.get("source"),
                    source_path=source_info.get("source_path"),
                    source_info=definition.source_info if definition else None,
                )
            )
        return tools

    @property
    def tool_registry(self) -> Dict[str, AgentTool]:
        """返回当前工具注册表副本。"""
        return dict(self._tool_registry)

    @property
    def tool_definitions(self) -> Dict[str, ToolDefinition]:
        return dict(self._tool_definitions)


__all__ = ["ToolsManager"]
