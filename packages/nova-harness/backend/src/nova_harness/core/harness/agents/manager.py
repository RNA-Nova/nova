"""AgentManager —— agents 注册表视图、当前角色旋钮与 yaml 写回（设计定案 §6）。

职责：

1. **注册表活视图**：agents 注册表来自 ``ResourceLoader.get_agents()``
   （包 + user/project 自动发现 + settings 条目，碰撞裁决 project > user >
   package），本管理器只读——每次访问现取 loader，reload 后无需手动刷新；
2. **旋钮**：``change_agent(name)`` 切换当前角色（未知名抛 ``ValueError``
   并列出可用名）；``current`` 读当前名。切换后的级联（ToolsManager
   agent_name、user_tools 重建、系统提示词重建）由 AgentSession 编排，
   不在本管理器内；
3. **默认解析归拢**（自 ``AgentSession._build_runtime`` 乔迁）：
   保持现状 > 显式指定 > 第一个可用 > ``"base_agent"``；显式名不存在
   即抛错（拼错名字不能静默落到别的 agent 上）；
4. **运行时视图**：``delegatable_agents()``（全部注册 agent——"只有
   agents，没有 subagents"，无主从划分）与菜单注入数据
   （``delegation_menu()``：name/description/source 标签）；
5. **CapabilitySelection 汇集点**：``get_capability_report()`` 透出当前
   角色各能力域（tools/extensions/user_tools/commands/skills/personas）的
   选配报告——各过滤点的报告由 AgentSession 编排汇集（manager 互不调用），
   本管理器经注入的 ``capability_report_provider`` 读取；
6. **yaml 写回**（``/agent save`` 落地）：``save_agent(as_name)`` 把
   当前生效状态（persona 引用、tools 激活集、名单字段、model）序列化为
   组合声明 yaml。写入分流红线：**包来源不可写**——影子写到
   ``<agent_dir>/agents/<name>.yaml``（user 级，优先级天然覆盖包）；
   user/project 来源就地写回原 yaml；``as_name`` 提供时按新名写到
   user 级。写盘后经 ``resource_loader.reload()`` 生效。

本管理器是 agents 这个 store 的唯一写回者（loader 管读、它管写——与
SettingsManager 独占 settings 写门同构）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml
from nova_harness.core.config.defaults import get_agent_dir
from nova_harness.core.types.protocols import ResourceLoaderProtocol
from nova_harness.core.types.resources.agents import AgentConfig
from nova_harness.core.types.resources.selection import CapabilitySelection

BASE_AGENT_NAME = "base_agent"
"""无可用 agent 时的兜底名（注册表为空时的合并默认）。"""

# save_agent 写回的三态名单字段（原文保留：None 不写、[] 写空列表）
_NAME_LIST_FIELDS = ("skills", "extensions", "user_tools", "commands")


def _source_tag(config: AgentConfig) -> str:
    """source 标签（``scope · origin``，取 source_info；无来源信息给空串）。"""
    info = config.source_info
    if info is None:
        return ""
    return " · ".join(p for p in (info.scope, info.origin) if p)


@dataclass
class AgentManager:
    """agents 注册表视图 + 当前角色旋钮（可变运行时容器，故 dataclass）。

    ``tools_manager`` / ``persona_manager`` 为后绑定引用（AgentSession 在
    ``_build_runtime`` 中织入）——save 取激活集/override 用；
    ``capability_report_provider`` 为报告数据源（同由 AgentSession 织入）。
    本管理器不调用它们以外的任何 manager。
    """

    resource_loader: Optional[ResourceLoaderProtocol] = None
    # user 级影子写回基准目录（``<agent_dir>/agents/<name>.yaml``）
    agent_dir: str = ""
    tools_manager: Optional[Any] = None
    persona_manager: Optional[Any] = None
    # 能力选配报告的数据源（AgentSession 编排汇集后注入——manager 互不调用，
    # 本管理器只做读取门）；缺省表示无报告生产者
    capability_report_provider: Optional[Callable[[], List[CapabilitySelection]]] = None
    _current: Optional[str] = None

    # ------------------------------------------------------------------
    # 注册表视图（活视图：现取 loader）
    # ------------------------------------------------------------------

    @property
    def agents(self) -> Dict[str, AgentConfig]:
        """agents 注册表活视图（{注册名: AgentConfig}）。"""
        if self.resource_loader is None:
            return {}
        get_agents = getattr(self.resource_loader, "get_agents", None)
        if get_agents is None:
            return {}
        result = get_agents()
        # Mock/占位 loader 的防御：非 dict 一律视为空注册表
        if not isinstance(result, dict):
            return {}
        return result

    def agent_names(self) -> List[str]:
        """可用 agent 名（字典序——与 loader.get_agent_names 同序）。"""
        return sorted(self.agents.keys())

    # ------------------------------------------------------------------
    # 当前角色旋钮
    # ------------------------------------------------------------------

    @property
    def current(self) -> str:
        """当前 agent 名（未解析过时给兜底名，不改变内部状态）。"""
        return self._current or BASE_AGENT_NAME

    def current_config(self) -> Optional[AgentConfig]:
        """当前 agent 的组合声明（注册表无此名时 None——reload 后被裁等）。"""
        return self.agents.get(self.current)

    def change_agent(self, name: str) -> None:
        """切换当前角色；未知名抛 ``ValueError``（列出可用名）。"""
        if name not in self.agents:
            available = ", ".join(self.agent_names()) or "(空)"
            raise ValueError(f"Agent '{name}' not found. Available agents: {available}")
        self._current = name

    def resolve_current(self, explicit: Optional[str] = None) -> str:
        """默认解析链：保持现状 > 显式指定 > 第一个可用 > ``base_agent``。

        显式名不存在即抛 ``ValueError``（拼错名字不能静默落到别的
        agent 上）。解析结果写入旋钮——reload 后经"保持现状"语义延续。
        """
        if self._current is not None:
            return self._current
        if explicit:
            if explicit not in self.agents:
                available = ", ".join(self.agent_names())
                raise ValueError(
                    f"Agent '{explicit}' not found."
                    + (
                        f" Available agents: {available}"
                        if available
                        else " No agents installed."
                    )
                )
            self._current = explicit
            return explicit
        names = self.agent_names()
        self._current = names[0] if names else BASE_AGENT_NAME
        return self._current

    # ------------------------------------------------------------------
    # 运行时视图（委派菜单 / 选择器数据）
    # ------------------------------------------------------------------

    def delegatable_agents(self) -> List[AgentConfig]:
        """全部注册 agent（按名字典序；无主从划分——只有 agents）。"""
        return [self.agents[name] for name in self.agent_names()]

    def agent_entries(self) -> List[Dict[str, Any]]:
        """选择器数据快照（name/description/scope/origin + current 标记）。"""
        entries: List[Dict[str, Any]] = []
        for config in self.delegatable_agents():
            info = config.source_info
            entries.append(
                {
                    "name": config.name,
                    "description": config.description or "",
                    "scope": info.scope if info else "",
                    "origin": info.origin if info else "",
                    "current": config.name == self.current,
                }
            )
        return entries

    def delegation_menu(self) -> List[Dict[str, str]]:
        """系统提示词 ``# Available Agents`` 段的数据（name/description/source）。"""
        return [
            {
                "name": config.name,
                "description": config.description or "",
                "source": _source_tag(config),
            }
            for config in self.delegatable_agents()
        ]

    # ------------------------------------------------------------------
    # CapabilitySelection 汇集点
    # ------------------------------------------------------------------

    def get_capability_report(self) -> List[CapabilitySelection]:
        """当前角色的能力选配报告（全部 yaml 可选配资源域）。

        报告的生产与汇集归 AgentSession（各过滤点产出、``_build_runtime``
        重建），本方法经注入的 provider 读取；无 provider 时为空报告。
        """
        if self.capability_report_provider is None:
            return []
        return list(self.capability_report_provider())

    # ------------------------------------------------------------------
    # yaml 写回（/agent save 落地——本管理器是 agents store 的唯一写回者）
    # ------------------------------------------------------------------

    def _serialize_current(self, name: str, model: Optional[str]) -> Dict[str, Any]:
        """把当前生效状态序列化为组合声明 dict（字段序即写盘序）。

        - ``model``：调用方给的当前模型 ref 优先；缺省保留原 yaml 偏好；
        - ``persona``：PersonaManager override 生效时写 override 名单条目，
          否则保留原 yaml 条目原文；
        - ``tools``：当前激活集（ToolsManager——面板等运行时修改后的生效面）；
          无 ToolsManager 时保留原 yaml 名单原文；
        - 名单字段（skills/extensions/user_tools/commands）：三态原文保留。
        """
        config = self.current_config()
        data: Dict[str, Any] = {"name": name}
        description = config.description if config else None
        if description:
            data["description"] = description
        if model:
            data["model"] = model
        elif config is not None and config.model:
            data["model"] = config.model

        override = (
            self.persona_manager.current_override if self.persona_manager else None
        )
        if override:
            data["persona"] = [override]
        elif config is not None and config.persona:
            data["persona"] = list(config.persona)

        if self.tools_manager is not None:
            data["tools"] = list(self.tools_manager.get_active_tools())
        elif config is not None and config.tools is not None:
            data["tools"] = [t.name for t in config.tools]

        if config is not None:
            for field_name in _NAME_LIST_FIELDS:
                value = getattr(config, field_name, None)
                if value is not None:
                    data[field_name] = list(value)
        return data

    def _resolve_write_path(self, as_name: Optional[str]) -> Path:
        """写入分流：as_name/包来源/无来源 → user 级；user/project 来源就地。"""
        config = self.current_config()
        agent_dir = self.agent_dir or str(get_agent_dir())
        if as_name is not None:
            return Path(agent_dir) / "agents" / f"{as_name}.yaml"
        info = config.source_info if config else None
        if info is not None and info.origin != "package" and info.path:
            # user/project 来源：就地写回原 yaml
            return Path(info.path)
        # 包来源（不可写红线）与无来源（base_agent 兜底）：影子写到 user 级
        return Path(agent_dir) / "agents" / f"{self.current}.yaml"

    async def save_agent(
        self, as_name: Optional[str] = None, model: Optional[str] = None
    ) -> Dict[str, Any]:
        """把当前生效状态物化为组合声明 yaml 并 reload 生效。

        ``as_name`` 提供时按新名写到 user 级（save-as 语义，不切换当前
        角色）；``model`` 为当前模型 ref（``provider/id``，编排层现取）。
        返回 ``{"name", "path", "shadowed"}``——``shadowed=True`` 表示
        包来源被影子写到 user 级（包内 yaml 未动）。
        """
        config = self.current_config()
        origin = config.source_info.origin if config and config.source_info else None
        target = self._resolve_write_path(as_name)
        name = as_name or self.current
        data = self._serialize_current(name, model)

        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                data, f, allow_unicode=True, sort_keys=False, default_flow_style=False
            )

        # 写盘后经 loader 重载生效（活视图自动反映新内容）
        if self.resource_loader is not None:
            await self.resource_loader.reload()

        return {
            "name": name,
            "path": str(target),
            "shadowed": as_name is None and origin == "package",
        }


__all__ = ["AgentManager", "BASE_AGENT_NAME"]
