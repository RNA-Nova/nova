"""工具定义类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, List, Optional

from nova_agent import ToolExecutionMode

if TYPE_CHECKING:
    from nova_harness.core.types.extensions import SourceInfo


@dataclass
class ToolDefinition:
    """Unified tool definition used by both extension tools and package-managed tools.

    执行体通过以下两种方式之一提供：
    - ``execute``: 直接的可调用对象（扩展工具或由 loader 绑定的包管理工具）。
    - ``executor_path``: 包管理工具所在的 ``executor.py`` 路径，由 ``ToolLoader`` 加载后填充 ``execute``。
    """

    name: str
    description: str
    parameters: dict = field(default_factory=dict)

    label: Optional[str] = None
    execution_mode: Optional[ToolExecutionMode] = None

    # 可选的渲染回调
    render_call: Optional[Callable[[Any], Optional[str]]] = None
    render_result: Optional[Callable[[Any], Optional[str]]] = None

    # 系统提示词元数据
    prompt_snippet: Optional[str] = None
    prompt_guidelines: Optional[List[str]] = None

    # 执行体（二选一）
    execute: Optional[Callable[..., Any]] = None
    executor_path: Optional[str] = None
    tool_dir: Optional[str] = None

    # 调用前参数转换
    prepare_arguments: Optional[Callable[..., Any]] = None

    # 来源信息
    source_info: Optional["SourceInfo"] = None


__all__ = ["ToolDefinition"]
