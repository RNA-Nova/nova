"""Tool loader — 工具的动态导入（单文件或目录形态）。

工具即代码：工具是 ``<name>.py`` 单文件（推荐，对齐 pi 的 ``tools/bash.ts``）
或 ``<name>/executor.py`` 目录（需要同目录资产时使用），暴露 ``Tool``
类（与 ``UserTool`` 对仗）：元数据由类属性声明（``name``/``description``/
``parameters`` 必需；``label``/``execution_mode``/``prepare_arguments``/
``prompt_snippet``/``prompt_guidelines`` 可选），执行由
``execute`` 方法提供——没有独立的元数据文件。

构造期注入 ``ToolContext``（cwd / settings 只读视图——不变量），工具实例
随资源加载创建、随 reload 重建；执行期的会话可变状态（当前模型）不进
构造期，由 ``DynamicTool`` 经 ``execute`` 第 5 参（``ToolExecContext``）
在每次调用时注入。

加载器只产出 ``ToolDefinition``（对齐 pi：loader 层定义、会话层包装）；
包装为 ``AgentTool`` 并注入 ``context_provider`` 统一发生在
``ToolsManager.refresh``。

工具不通过文件系统自动发现；``PackageResolver`` 会从已安装包中解析出具体
的工具路径，并通过 ``additional_paths`` 传给 ``ToolLoader``。
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from nova_harness.core.resources.source_info import (
    find_source_info_for_path,
    source_info_from_metadata,
)
from nova_harness.core.types.extensions import SourceInfo
from nova_harness.core.types.package import ResolvedResource
from nova_harness.core.types.resources.diagnostics import (
    ResourceCollision,
    ResourceDiagnostic,
)
from nova_harness.core.types.resources.tools import (
    NULL_TOOL_SETTINGS,
    ToolContext,
    ToolDefinition,
)
from nova_harness.core.utils.files import canonicalize_path

# Tool 类必须声明的元数据属性及期望类型
_REQUIRED_ATTRS: Dict[str, type] = {
    "name": str,
    "description": str,
    "parameters": dict,
}

# 可选元数据属性及默认值
_OPTIONAL_ATTRS: Dict[str, Any] = {
    "label": None,
    "execution_mode": None,
    "prepare_arguments": None,
    "prompt_snippet": None,
    "prompt_guidelines": None,
}


def _import_executor_module(
    executor_path: str, name_hint: str
) -> tuple[Optional[Any], Optional[ResourceDiagnostic]]:
    """动态导入 executor.py 模块对象。

    单次导入：导入失败直接返回诊断，不会重复触发模块级副作用。
    """
    # 将特殊字符替换为下划线，避免 module_name 非法。
    safe_name = "".join(c if c.isalnum() or c == "_" else "_" for c in name_hint)
    module_name = f"_nova_harness_dynamic_tool_{safe_name}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, executor_path)
        if spec is None or spec.loader is None:
            return None, ResourceDiagnostic(
                category="error",
                message=f"Cannot create module spec for executor: {executor_path}",
                path=executor_path,
            )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, None
    except Exception as exc:
        return None, ResourceDiagnostic(
            category="error",
            message=f"Failed to import tool executor: {exc}",
            path=executor_path,
        )


def _load_tool(
    tool_path: str,
    context: ToolContext,
    source_info: Optional[SourceInfo] = None,
    diagnostics: Optional[List[ResourceDiagnostic]] = None,
) -> Optional[ToolDefinition]:
    """从单个工具（单文件或目录形态）加载为 ``ToolDefinition``。

    形态规则：
    - ``<name>.py`` 单文件：该文件即工具（对齐 pi 的 ``tools/bash.ts``）；
    - ``<name>/executor.py`` 目录：需要同目录资产时使用的形态。

    只产出 definition（``execute`` 绑定为 executor 实例方法）；包装为
    ``AgentTool`` 统一在 ``ToolsManager.refresh`` 完成。
    """

    def _diag(message: str, path: str) -> None:
        if diagnostics is not None:
            diagnostics.append(
                ResourceDiagnostic(category="error", message=message, path=path)
            )

    p = Path(tool_path)
    if p.is_file():
        executor_path = str(p)
        tool_dir = str(p.parent)
    else:
        executor_path = os.path.join(tool_path, "executor.py")
        tool_dir = tool_path
    if not os.path.exists(executor_path):
        _diag(f"Tool missing executor: {tool_path}", tool_path)
        return None

    module, diag = _import_executor_module(
        executor_path, p.stem if p.is_file() else p.name
    )
    if module is None:
        if diag is not None and diagnostics is not None:
            diagnostics.append(diag)
        return None

    cls = getattr(module, "Tool", None)
    if cls is None:
        _diag("executor does not define Tool class", executor_path)
        return None

    # 元数据即类属性：加载时校验，缺失/类型错误走诊断
    attrs: Dict[str, Any] = {}
    for attr, expected in _REQUIRED_ATTRS.items():
        value = getattr(cls, attr, None)
        if value is None:
            _diag(
                f"Tool missing required class attribute '{attr}'",
                executor_path,
            )
            return None
        if not isinstance(value, expected):
            _diag(
                f"Tool class attribute '{attr}' must be "
                f"{expected.__name__}, got {type(value).__name__}",
                executor_path,
            )
            return None
        attrs[attr] = value
    for attr, default in _OPTIONAL_ATTRS.items():
        attrs[attr] = getattr(cls, attr, default)

    try:
        executor = cls(context)
    except Exception as exc:
        _diag(f"Failed to instantiate Tool: {exc}", executor_path)
        return None

    # 统一执行体签名：(tool_call_id, params, signal, on_update, ctx)
    definition = ToolDefinition(
        name=attrs["name"],
        description=attrs["description"],
        parameters=attrs["parameters"],
        label=attrs["label"],
        execution_mode=attrs["execution_mode"],
        prepare_arguments=attrs["prepare_arguments"],
        prompt_snippet=attrs["prompt_snippet"],
        prompt_guidelines=attrs["prompt_guidelines"],
        executor_path=executor_path,
        tool_dir=tool_dir,
        source_info=source_info,
    )
    definition.execute = executor.execute
    return definition


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
        tool_context: Optional[ToolContext] = None,
    ) -> None:
        self._agent_dir = str(agent_dir) if agent_dir else None
        self._cwd = str(cwd) if cwd else None
        self._additional_paths = [str(p) for p in (additional_paths or []) if p]
        self._resolved_resources = resolved_resources or []
        self._extension_source_infos = extension_source_infos or []
        self._no_tools = no_tools
        self._allowed_names = allowed_names
        self._tool_context = tool_context or ToolContext(
            cwd=self._cwd or os.getcwd(),
            settings=NULL_TOOL_SETTINGS,
        )
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

    def load_tools(self) -> Dict[str, ToolDefinition]:
        """加载所有可用工具并返回 ``{name: ToolDefinition}``。"""
        self._diagnostics.clear()
        result: Dict[str, ToolDefinition] = {}
        tool_dirs: Dict[str, str] = {}
        if self._no_tools:
            return result

        seen_paths: set = set()

        def record_collision(tool_name: str, loser_dir: str) -> None:
            winner_dir = tool_dirs.get(tool_name)
            self._diagnostics.append(
                ResourceDiagnostic(
                    category="collision",
                    message=f"Tool '{tool_name}' from {loser_dir} shadowed by {winner_dir}",
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
            if not p.exists():
                continue
            real_dir = canonicalize_path(str(p))
            if real_dir in seen_paths:
                continue
            seen_paths.add(real_dir)

            # 形态判定：.py 文件或含 executor.py 的目录本身即工具；
            # 否则当作容器目录，加载其含 executor.py 的直接子目录
            if p.is_file() and p.suffix == ".py":
                candidates = [p]
            elif p.is_dir() and (p / "executor.py").exists():
                candidates = [p]
            elif p.is_dir():
                candidates = [
                    child
                    for child in sorted(p.iterdir())
                    if child.is_dir() and (child / "executor.py").exists()
                ]
            else:
                continue
            for candidate in candidates:
                candidate_real = canonicalize_path(str(candidate))
                if candidate_real != real_dir and candidate_real in seen_paths:
                    continue
                seen_paths.add(candidate_real)
                definition = _load_tool(
                    str(candidate),
                    context=self._tool_context,
                    source_info=source_info or self._source_info_for(str(candidate)),
                    diagnostics=self._diagnostics,
                )
                if definition is None:
                    continue
                if (
                    self._allowed_names is not None
                    and definition.name not in self._allowed_names
                ):
                    continue
                # 同名碰撞 first-wins：路径已按优先级排序，先加载者胜出
                if definition.name in result:
                    record_collision(definition.name, str(candidate))
                    continue
                result[definition.name] = definition
                tool_dirs[definition.name] = str(candidate)

        return result

    def get_diagnostics(self) -> List[ResourceDiagnostic]:
        """返回最近一次 ``load_tools()`` 产生的诊断信息。"""
        return list(self._diagnostics)


__all__ = [
    "ToolLoader",
]
