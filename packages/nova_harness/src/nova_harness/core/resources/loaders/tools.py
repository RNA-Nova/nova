"""Tool loader — filesystem IO and dynamic executor import.

负责从调用方显式提供的工具目录路径加载工具定义及其执行器，并返回可直接注册到
``Agent`` 的 ``AgentTool`` 对象。

工具目录不再通过文件系统自动发现；``PackageResolver`` 会从已安装包中解析出具体
的 tool 路径，并通过 ``additional_paths`` 传给 ``ToolLoader``。
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from nova_agent import AgentTool

from nova_harness.core.resources.source_info import (
    find_source_info_for_path,
    source_info_from_metadata,
)
from nova_harness.core.types.extensions import SourceInfo
from nova_harness.core.types.package_manager import ResolvedResource
from nova_harness.core.types.resources.diagnostics import (
    ResourceCollision,
    ResourceDiagnostic,
)
from nova_harness.core.types.runtime.tools import ToolDefinition
from nova_harness.core.utils.files import (
    canonicalize_path,
    load_json_file,
    load_text_file,
)


def load_tool_definition(
    tool_dir: str, source_info: Optional[SourceInfo] = None
) -> Optional[ToolDefinition]:
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
        source_info=source_info,
    )


def _load_executor(executor_path: str, tool_name: str) -> Optional[Any]:
    """Dynamically import executor.py and instantiate ToolExecutor."""
    try:
        # 将特殊字符替换为下划线，避免 module_name 非法。
        safe_name = "".join(c if c.isalnum() or c == "_" else "_" for c in tool_name)
        module_name = f"_nova_harness_dynamic_tool_{safe_name}"
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


def _diagnose_tool_failure(
    tool_dir: str,
    source_info: Optional[SourceInfo] = None,
) -> Optional[ResourceDiagnostic]:
    """分析工具目录加载失败原因，返回 ResourceDiagnostic；成功或无法诊断时返回 None。"""
    schema_json = os.path.join(tool_dir, "schema.json")
    if not os.path.exists(schema_json):
        return ResourceDiagnostic(
            category="error",
            message=f"Tool directory missing schema.json: {tool_dir}",
            path=tool_dir,
        )

    data = load_json_file(schema_json)
    if not isinstance(data, dict):
        return ResourceDiagnostic(
            category="error",
            message="Tool schema.json is not a JSON object",
            path=schema_json,
        )

    name = data.get("name")
    if not name:
        return ResourceDiagnostic(
            category="error",
            message="Tool schema.json missing 'name' field",
            path=schema_json,
        )

    executor_py = os.path.join(tool_dir, "executor.py")
    if not os.path.exists(executor_py):
        # 仅声明 schema 的工具不视为错误，由调用方提供 executor。
        return None

    try:
        safe_name = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
        module_name = f"_nova_harness_dynamic_tool_{safe_name}"
        spec = importlib.util.spec_from_file_location(module_name, executor_py)
        if spec is None or spec.loader is None:
            return ResourceDiagnostic(
                category="error",
                message=f"Cannot create module spec for executor: {executor_py}",
                path=executor_py,
            )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:
        return ResourceDiagnostic(
            category="error",
            message=f"Failed to import tool executor: {exc}",
            path=executor_py,
        )

    if not hasattr(module, "ToolExecutor"):
        return ResourceDiagnostic(
            category="error",
            message="executor.py does not define ToolExecutor class",
            path=executor_py,
        )

    try:
        cls = getattr(module, "ToolExecutor")
        cls()
    except Exception as exc:
        return ResourceDiagnostic(
            category="error",
            message=f"Failed to instantiate ToolExecutor: {exc}",
            path=executor_py,
        )

    return None


def _load_tool_from_dir(
    tool_dir: str,
    source_info: Optional[SourceInfo] = None,
    diagnostics: Optional[List[ResourceDiagnostic]] = None,
) -> Optional[AgentTool]:
    """从单个工具目录加载并包装成 AgentTool。"""
    from nova_harness.core.harness.tools.dynamic_tool import DynamicTool

    definition = load_tool_definition(tool_dir, source_info=source_info)
    if definition is None:
        diag = _diagnose_tool_failure(tool_dir, source_info=source_info)
        if diag is not None and diagnostics is not None:
            diagnostics.append(diag)
        return None

    if not definition.executor_path:
        # Schema-only tool — caller must supply executor elsewhere.
        return None

    executor = _load_executor(definition.executor_path, definition.name)
    if executor is None:
        diag = _diagnose_tool_failure(tool_dir, source_info=source_info)
        if diag is not None and diagnostics is not None:
            diagnostics.append(diag)
        return None

    # 统一执行体签名：(tool_call_id, params, signal, on_update)
    definition.execute = executor.execute
    return DynamicTool(definition)


class ToolLoader:
    """从显式路径加载工具。

    不再扫描 ``~/.nova/agent/tools/`` 或 ``./.nova/tools/``。所有工具路径必须由
    ``PackageResolver`` 解析后通过 ``additional_paths`` 传入，以保证 tool 的
    Python 依赖已通过包安装正确安装。
    """

    def __init__(
        self,
        agent_dir: Optional[Union[str, Path]] = None,
        cwd: Optional[Union[str, Path]] = None,
        additional_paths: Optional[List[Union[str, Path]]] = None,
        resolved_resources: Optional[List[ResolvedResource]] = None,
        extension_source_infos: Optional[List[SourceInfo]] = None,
        no_tools: bool = False,
        allowed_names: Optional[set[str]] = None,
    ) -> None:
        self._agent_dir = str(agent_dir) if agent_dir else None
        self._cwd = str(cwd) if cwd else None
        self._additional_paths = [str(p) for p in (additional_paths or []) if p]
        self._resolved_resources = resolved_resources or []
        self._extension_source_infos = extension_source_infos or []
        self._no_tools = no_tools
        self._allowed_names = allowed_names
        self._diagnostics: List[ResourceDiagnostic] = []

        # 优先使用 resolver 提供的精确 metadata
        self._source_info_by_path: Dict[str, SourceInfo] = {}
        for resource in self._resolved_resources:
            if not resource.enabled:
                continue
            self._source_info_by_path[str(Path(resource.path).resolve())] = (
                source_info_from_metadata(resource)
            )

    def _source_info_for(self, tool_dir: str) -> Optional[SourceInfo]:
        """返回工具目录对应的 SourceInfo（精确或前缀匹配）。"""
        resolved = str(Path(tool_dir).resolve())
        if resolved in self._source_info_by_path:
            return self._source_info_by_path[resolved]
        return find_source_info_for_path(resolved, self._extension_source_infos)

    def load_tools(self) -> Dict[str, AgentTool]:
        """加载所有可用工具并返回 ``{name: AgentTool}``。"""
        self._diagnostics.clear()
        result: Dict[str, AgentTool] = {}
        tool_dirs: Dict[str, str] = {}
        if self._no_tools:
            return result

        seen_paths: set = set()

        def record_collision(tool_name: str, loser_dir: str) -> None:
            winner_dir = tool_dirs.get(tool_name)
            self._diagnostics.append(
                ResourceDiagnostic(
                    category="collision",
                    message=(
                        f"Tool '{tool_name}' from {loser_dir} shadows "
                        f"previously loaded tool from {winner_dir}"
                    ),
                    path=loser_dir,
                    collision=ResourceCollision(
                        resource_type="tool",
                        name=tool_name,
                        winner_path=winner_dir,
                        loser_path=loser_dir,
                    ),
                )
            )

        for tool_dir in self._additional_paths:
            source_info = self._source_info_for(tool_dir)
            p = Path(tool_dir)
            if not p.exists() or not p.is_dir():
                continue
            real_dir = canonicalize_path(str(p))
            if real_dir in seen_paths:
                continue
            seen_paths.add(real_dir)

            # 如果路径本身是合法工具目录，直接加载
            if (p / "schema.json").exists():
                tool = _load_tool_from_dir(
                    str(p), source_info=source_info, diagnostics=self._diagnostics
                )
                if tool is None:
                    continue
                if (
                    self._allowed_names is not None
                    and tool.name not in self._allowed_names
                ):
                    continue
                if tool.name in result:
                    record_collision(tool.name, str(p))
                result[tool.name] = tool
                tool_dirs[tool.name] = str(p)
                continue

            # 否则当作容器目录，加载其直接子目录
            for name in sorted(os.listdir(p)):
                child_path = p / name
                if not child_path.is_dir():
                    continue
                child_real = canonicalize_path(str(child_path))
                if child_real in seen_paths:
                    continue
                seen_paths.add(child_real)
                child_source_info = source_info or self._source_info_for(
                    str(child_path)
                )
                tool = _load_tool_from_dir(
                    str(child_path),
                    source_info=child_source_info,
                    diagnostics=self._diagnostics,
                )
                if tool is None:
                    continue
                if (
                    self._allowed_names is not None
                    and tool.name not in self._allowed_names
                ):
                    continue
                if tool.name in result:
                    record_collision(tool.name, str(child_path))
                result[tool.name] = tool
                tool_dirs[tool.name] = str(child_path)

        return result

    def get_diagnostics(self) -> List[ResourceDiagnostic]:
        """返回最近一次 ``load_tools()`` 产生的诊断信息。"""
        return list(self._diagnostics)


__all__ = [
    "ToolLoader",
    "load_json_file",
    "load_text_file",
    "load_tool_definition",
]
