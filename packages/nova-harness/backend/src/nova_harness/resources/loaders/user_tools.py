"""User tool loader — 用户工具目录的 executor.py 动态导入。

用户工具即代码：一个用户工具目录只含 ``executor.py``，暴露一个
``UserTool`` 类：

```python
class UserTool:
    name = "bash"                          # 必需：碰撞/白名单的判别键
    description = "..."                    # 必需
    parameters = {...}                     # 必需：JSON Schema（给前端渲染表单）
    MESSAGE_TYPES = [BashExecutionMessage] # 可选：加载时注册进消息回载注册表

    def __init__(self, session): ...       # 构造注入会话上下文
    async def execute(self, params, on_event, signal): ...
    def message_from_result(self, params, result): ...  # 可选：扩展拦截
                                           # 事件（user_bash）返回完整
                                           # result 时的消息转换器
```

元数据是类属性——import 即可读，无需实例（白名单/碰撞检测发生在会话
绑定之前）；按会话实例化，会话上下文经构造注入。

与 LLM 工具加载（``loaders/tools.py``）同型：路径全部由 ``PackageResolver``
解析后经 ``additional_paths`` 传入，不做文件系统自动发现。
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from nova_harness.core.harness.session.message_types import (
    register_message_types,
)
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
from nova_harness.core.types.resources.user_tools import (
    UserToolDefinition,
    UserToolResource,
)
from nova_harness.core.utils.files import canonicalize_path

# UserTool 类必须声明的元数据属性及期望类型
_REQUIRED_ATTRS: Dict[str, type] = {
    "name": str,
    "description": str,
    "parameters": dict,
}


def _import_executor_module(
    executor_path: str, name_hint: str
) -> Tuple[Optional[Any], Optional[ResourceDiagnostic]]:
    """动态导入 executor.py 模块对象。"""
    safe_name = "".join(c if c.isalnum() or c == "_" else "_" for c in name_hint)
    module_name = f"_nova_harness_dynamic_user_tool_{safe_name}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, executor_path)
        if spec is None or spec.loader is None:
            return None, ResourceDiagnostic(
                category="error",
                message=f"Cannot create module spec for user tool executor: {executor_path}",
                path=executor_path,
            )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, None
    except Exception as exc:
        return None, ResourceDiagnostic(
            category="error",
            message=f"Failed to import user tool executor: {exc}",
            path=executor_path,
        )


def _load_user_tool(
    tool_path: str,
    source_info: Optional[SourceInfo] = None,
    diagnostics: Optional[List[ResourceDiagnostic]] = None,
) -> Optional[UserToolResource]:
    """从单个用户工具（单文件或目录形态）加载 UserTool 类并包装为资源。"""
    p = Path(tool_path)
    executor_path = str(p) if p.is_file() else os.path.join(tool_path, "executor.py")
    if not os.path.exists(executor_path):
        if diagnostics is not None:
            diagnostics.append(
                ResourceDiagnostic(
                    category="error",
                    message=f"User tool missing executor: {tool_path}",
                    path=tool_path,
                )
            )
        return None

    module, diag = _import_executor_module(
        executor_path, p.stem if p.is_file() else p.name
    )
    if module is None:
        if diag is not None and diagnostics is not None:
            diagnostics.append(diag)
        return None

    cls = getattr(module, "UserTool", None)
    if cls is None:
        if diagnostics is not None:
            diagnostics.append(
                ResourceDiagnostic(
                    category="error",
                    message="executor.py does not define UserTool class",
                    path=executor_path,
                )
            )
        return None

    # 元数据即类属性：加载时校验
    attrs: Dict[str, Any] = {}
    for attr, expected in _REQUIRED_ATTRS.items():
        value = getattr(cls, attr, None)
        if value is None:
            if diagnostics is not None:
                diagnostics.append(
                    ResourceDiagnostic(
                        category="error",
                        message=f"UserTool missing required class attribute '{attr}'",
                        path=executor_path,
                    )
                )
            return None
        if not isinstance(value, expected):
            if diagnostics is not None:
                diagnostics.append(
                    ResourceDiagnostic(
                        category="error",
                        message=(
                            f"UserTool class attribute '{attr}' must be "
                            f"{expected.__name__}, got {type(value).__name__}"
                        ),
                        path=executor_path,
                    )
                )
            return None
        attrs[attr] = value

    # 消息类型回载注册：加载时注册（早于会话 JSONL 解析）
    message_types = getattr(cls, "MESSAGE_TYPES", None)
    if isinstance(message_types, (list, tuple)) and message_types:
        register_message_types(list(message_types))

    name = attrs["name"]
    description = attrs["description"]
    parameters = attrs["parameters"]

    def _create(session: Any) -> UserToolDefinition:
        """按会话实例化 UserTool 并包装为定义。"""
        instance = cls(session)
        return UserToolDefinition(
            name=name,
            description=description,
            parameters=dict(parameters),
            execute=instance.execute,
            # 可选鸭子类型方法（与 MESSAGE_TYPES 同型）：扩展拦截事件
            # 返回完整 result 时，会话层经它把 result 翻译为本工具的消息
            build_result_message=getattr(instance, "message_from_result", None),
            source_info=source_info,
        )

    return UserToolResource(
        name=name,
        description=description,
        parameters=parameters,
        create=_create,
        source_info=source_info,
    )


class UserToolLoader:
    """从显式路径加载用户工具。

    与 ``ToolLoader`` 同一纪律：不扫描默认目录，所有路径来自
    ``PackageResolver``（经 ``additional_paths``）。
    """

    def __init__(
        self,
        additional_paths: Optional[List[str]] = None,
        resolved_resources: Optional[List[ResolvedResource]] = None,
        extension_source_infos: Optional[List[SourceInfo]] = None,
        no_user_tools: bool = False,
    ) -> None:
        self._additional_paths = [str(p) for p in (additional_paths or []) if p]
        self._resolved_resources = resolved_resources or []
        self._extension_source_infos = extension_source_infos or []
        self._no_user_tools = no_user_tools
        self._diagnostics: List[ResourceDiagnostic] = []

        self._source_info_by_path: Dict[str, SourceInfo] = {}
        for resource in self._resolved_resources:
            if not resource.enabled:
                continue
            self._source_info_by_path[str(Path(resource.path).resolve())] = (
                source_info_from_metadata(resource)
            )

    def _source_info_for(self, tool_dir: str) -> Optional[SourceInfo]:
        resolved = str(Path(tool_dir).resolve())
        if resolved in self._source_info_by_path:
            return self._source_info_by_path[resolved]
        return find_source_info_for_path(resolved, self._extension_source_infos)

    def load_user_tools(self) -> Dict[str, UserToolResource]:
        """加载所有可用用户工具并返回 ``{name: UserToolResource}``。"""
        self._diagnostics.clear()
        result: Dict[str, UserToolResource] = {}
        tool_dirs: Dict[str, str] = {}
        if self._no_user_tools:
            return result

        seen_paths: set = set()

        def record_collision(tool_name: str, loser_dir: str) -> None:
            winner_dir = tool_dirs.get(tool_name)
            self._diagnostics.append(
                ResourceDiagnostic(
                    category="collision",
                    message=f"User tool '{tool_name}' from {loser_dir} shadowed by {winner_dir}",
                    path=loser_dir,
                    collision=ResourceCollision(
                        resource_type="user_tool",
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

            # 形态判定与 ToolLoader 一致：.py 文件或含 executor.py 的目录
            # 本身即工具；否则当容器目录扫描子目录
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
                resource = _load_user_tool(
                    str(candidate),
                    source_info=source_info or self._source_info_for(str(candidate)),
                    diagnostics=self._diagnostics,
                )
                if resource is None:
                    continue
                # 同名碰撞 first-wins：路径已按优先级排序
                if resource.name in result:
                    record_collision(resource.name, str(candidate))
                    continue
                result[resource.name] = resource
                tool_dirs[resource.name] = str(candidate)

        return result

    def get_diagnostics(self) -> List[ResourceDiagnostic]:
        """返回最近一次 ``load_user_tools()`` 产生的诊断信息。"""
        return list(self._diagnostics)


__all__ = [
    "UserToolLoader",
]
