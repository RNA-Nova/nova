"""Agent 组合声明加载实现（三层模型：素材/组合/运行）。

agent = ``agents/<name>.yaml`` **纯组合声明文件**——只负责"选什么"：
人格文本（``persona:`` 条目列表，顺序即组装顺序）+ 能力选配
（tools/extensions/user_tools/commands/skills）+ 元数据。

**persona 升格后本模块只做解析，不做装配**：``persona:`` 原始条目原样存入
``AgentConfig.persona``，``sections`` 恒为空——装配（路径引用读文件 /
注册名查 persona 注册表）由会话期的 ``PersonaManager`` 完成（按名引用
必须等注册表就绪，见 ``core/harness/persona/manager.py``）。
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from nova_harness.core.types.resources.agents import AgentConfig
from nova_harness.core.types.resources.diagnostics import ResourceDiagnostic
from nova_harness.core.types.resources.tools import ToolInfo


def _parse_tool_items(items: Any) -> Optional[List[ToolInfo]]:
    """把 yaml 中的 tool 条目解析为 ToolInfo 列表（字符串或带 name 的对象）。

    三态：键缺席/为 null → ``None``（不设防）；显式 ``[]`` → ``[]``（全禁）。
    字符串条目可带 ``!`` 排除前缀（裁决在 name_sets，此处原样保留）。
    """
    if items is None:
        return None
    tools: List[ToolInfo] = []
    seen: set = set()

    for item in items:
        if isinstance(item, str):
            name = item.strip()
            if name and name not in seen:
                seen.add(name)
                tools.append(ToolInfo(name=name, description=""))
        elif isinstance(item, dict) and "name" in item:
            name = str(item["name"]).strip()
            if name and name not in seen:
                seen.add(name)
                tools.append(
                    ToolInfo(
                        name=name,
                        description=item.get("description", ""),
                        parameters=item.get("parameters"),
                        prompt_snippet=item.get("prompt_snippet"),
                        prompt_guidelines=item.get("prompt_guidelines"),
                    )
                )

    return tools


def _parse_string_list(items: Any) -> Optional[List[str]]:
    """把 yaml 列表过滤为去重后的字符串列表（非字符串项忽略）。

    三态：键缺席/为 null → ``None``（不设防）；显式 ``[]`` → ``[]``（全禁）。
    """
    if items is None:
        return None
    seen: set = set()
    result: List[str] = []
    for item in items:
        if isinstance(item, str):
            value = item.strip()
            if value and value not in seen:
                seen.add(value)
                result.append(value)
    return result


def load_agent_config_from_yaml(
    yaml_path: str,
) -> Tuple[Optional[AgentConfig], List[ResourceDiagnostic]]:
    """加载单个组合声明 yaml 为 AgentConfig（+ 诊断列表）。

    文件不存在/解析失败/非 mapping → ``(None, [diagnostic])``。
    ``name`` 缺省时取文件名（``agents/coding_agent.yaml`` → ``coding_agent``）。
    """
    path = Path(yaml_path)
    if not path.is_file():
        return None, [
            ResourceDiagnostic(
                category="warning", message="agent 组合声明不存在", path=str(path)
            )
        ]

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        return None, [
            ResourceDiagnostic(
                category="warning",
                message=f"agent yaml 解析失败: {exc}",
                path=str(path),
            )
        ]
    if not isinstance(data, dict):
        return None, [
            ResourceDiagnostic(
                category="warning",
                message="agent yaml 顶层必须是 mapping",
                path=str(path),
            )
        ]

    config = AgentConfig(
        name=str(data.get("name") or path.stem),
        agent_dir=str(path.parent),
        description=data.get("description"),
        model=data.get("model"),
        persona=_parse_string_list(data.get("persona")) or [],
        tools=_parse_tool_items(data.get("tools")),
        skills=_parse_string_list(data.get("skills")),
        extensions=_parse_string_list(data.get("extensions")),
        user_tools=_parse_string_list(data.get("user_tools")),
        commands=_parse_string_list(data.get("commands")),
    )
    return config, []


def load_agents(base_dir: str) -> Dict[str, AgentConfig]:
    """扫描 *base_dir* 顶层的 ``*.yaml`` 组合声明（一文件一 agent）。

    返回 ``{name: AgentConfig}``；目录不存在或无 yaml 时返回空 dict。
    （诊断面由 ``load_agent_config_from_yaml`` 的调用方各自收集。）
    """
    results: Dict[str, AgentConfig] = {}
    if not os.path.isdir(base_dir):
        return results
    for child in sorted(os.listdir(base_dir)):
        if not child.endswith((".yaml", ".yml")):
            continue
        path = os.path.join(base_dir, child)
        if not os.path.isfile(path):
            continue
        config, _diagnostics = load_agent_config_from_yaml(path)
        if config is not None:
            results[config.name] = config
    return results


__all__ = [
    "load_agent_config_from_yaml",
    "load_agents",
]
