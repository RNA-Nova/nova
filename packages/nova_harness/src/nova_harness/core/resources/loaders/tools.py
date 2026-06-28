"""Tool loader — filesystem IO and dynamic executor import.

负责从包管理器安装的目录（``~/.nova/agent/tools/`` 与项目级 ``./.nova/tools/``）
加载工具定义及其执行器，并返回可直接注册到 ``Agent`` 的 ``AgentTool`` 对象。

这是工具资源的**唯一加载入口**；``harness/tools/registry.py`` 与 ``AgentSession``
均通过本模块或 ``ResourceLoader.get_tools()`` 获取工具，不再自行扫描目录。
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from nova_agent import AgentTool

from nova_harness.core.config.defaults import CONFIG_DIR_NAME
from nova_harness.core.types.diagnostics import ResourceDiagnostic
from nova_harness.core.types.tools import ToolDefinition


def load_text_file(file_path: str) -> Optional[str]:
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            return content if content else None
    except (IOError, UnicodeDecodeError):
        return None


def load_json_file(file_path: str) -> Optional[dict]:
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def load_tool_definition(tool_dir: str) -> Optional[ToolDefinition]:
    """Load a single tool definition from its directory."""
    schema_json = os.path.join(tool_dir, "schema.json")
    data = load_json_file(schema_json)
    if not isinstance(data, dict):
        return None

    name = data.get("name")
    if not name:
        return None

    executor_py = os.path.join(tool_dir, "executor.py")

    return ToolDefinition(
        name=name,
        description=data.get("description", ""),
        parameters=data.get("parameters", {}),
        prompt_snippet=data.get("prompt_snippet"),
        prompt_guidelines=data.get("prompt_guidelines"),
        executor_path=executor_py if os.path.exists(executor_py) else None,
        tool_dir=tool_dir,
    )


def _load_executor(executor_path: str, tool_name: str) -> Optional[Any]:
    """Dynamically import executor.py and instantiate ToolExecutor."""
    try:
        module_name = f"_nova_harness_dynamic_tool_{tool_name}"
        spec = importlib.util.spec_from_file_location(module_name, executor_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if hasattr(module, "ToolExecutor"):
            cls = getattr(module, "ToolExecutor")
            return cls()

        return None
    except Exception:
        return None


def _load_tool_from_dir(tool_dir: str) -> Optional[AgentTool]:
    """从单个工具目录加载并包装成 AgentTool。"""
    from nova_harness.core.types.tools import DynamicTool

    definition = load_tool_definition(tool_dir)
    if definition is None:
        return None

    if not definition.executor_path:
        # Schema-only tool — caller must supply executor elsewhere.
        return None

    executor = _load_executor(definition.executor_path, definition.name)
    if executor is None:
        return None

    # 统一执行体签名：(tool_call_id, params, signal, on_update)
    definition.execute = executor.execute
    return DynamicTool(definition)


class ToolLoader:
    """从文件系统统一加载包管理工具。

    扫描路径（按优先级递增）：
    1. ``agent_dir/tools/``（全局）
    2. ``cwd/.nova/tools/``（项目级）
    3. ``additional_paths``（扩展或调用方显式贡献）
    """

    def __init__(
        self,
        agent_dir: Optional[Union[str, Path]] = None,
        cwd: Optional[Union[str, Path]] = None,
        additional_paths: Optional[List[Union[str, Path]]] = None,
        no_tools: bool = False,
    ) -> None:
        self._agent_dir = str(agent_dir) if agent_dir else None
        self._cwd = str(cwd) if cwd else None
        self._additional_paths = [str(p) for p in (additional_paths or []) if p]
        self._no_tools = no_tools
        self._diagnostics: List[ResourceDiagnostic] = []

    def load_tools(self) -> Dict[str, AgentTool]:
        """加载所有可用工具并返回 ``{name: AgentTool}``。"""
        self._diagnostics.clear()
        result: Dict[str, AgentTool] = {}
        if self._no_tools:
            return result

        for tool_dir in self._discover_tool_dirs():
            tool = _load_tool_from_dir(tool_dir)
            if tool is None:
                continue

            if tool.name in result:
                self._diagnostics.append(
                    ResourceDiagnostic(
                        category="warning",
                        message=(
                            f"Tool '{tool.name}' from {tool_dir} overrides "
                            "a previously loaded tool with the same name"
                        ),
                        path=tool_dir,
                    )
                )
            result[tool.name] = tool

        return result

    def get_diagnostics(self) -> List[ResourceDiagnostic]:
        """返回最近一次 ``load_tools()`` 产生的诊断信息。"""
        return list(self._diagnostics)

    def _discover_tool_dirs(self) -> List[str]:
        """发现所有待加载的工具目录。"""
        bases: List[Path] = []

        if self._agent_dir:
            bases.append(Path(self._agent_dir) / "tools")

        if self._cwd:
            bases.append(Path(self._cwd) / CONFIG_DIR_NAME / "tools")

        for path in self._additional_paths:
            bases.append(Path(path))

        dirs: List[str] = []
        for base in bases:
            if not base.exists() or not base.is_dir():
                continue
            for name in sorted(os.listdir(base)):
                tool_path = base / name
                if tool_path.is_dir():
                    dirs.append(str(tool_path))

        return dirs


__all__ = [
    "ToolLoader",
    "load_json_file",
    "load_text_file",
    "load_tool_definition",
]
