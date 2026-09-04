"""工具资源类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Protocol

from nova_agent import ToolExecutionMode
from nova_ai import Model
from nova_ai.types.base_model import NovaBaseModel

from nova_harness.core.types.extensions.source import SourceInfo
from nova_harness.core.types.ui.context import UIContext
from nova_harness.core.types.ui.noop import NoOpUIContext

if TYPE_CHECKING:
    # 仅类型检查视角（运行期不导入——agents.py 反向 import 本模块的 ToolInfo，
    # 运行期导入构成循环）
    from nova_harness.core.types.resources.agents import AgentConfig

_SHARED_NOOP_UI = NoOpUIContext()
"""无 UI 宿主时的共享空实现（无状态，可安全共享作默认值）。"""


class ToolInfo(NovaBaseModel):
    """工具展示元数据（agent.yaml tools 条目 / 工具列表的对外形态）。"""

    name: str
    description: str
    parameters: Optional[Dict[str, Any]] = None
    prompt_snippet: Optional[str] = None
    prompt_guidelines: Optional[List[str]] = None
    source: Optional[str] = None
    source_path: Optional[str] = None
    # 工具来源的完整元数据（包安装位置/scope）；agent.yaml 解析时不填
    source_info: Optional["SourceInfo"] = None


class ToolSettingsView(Protocol):
    """包工具可见的 settings 只读视图。

    结构化 Protocol：``SettingsManager`` 天然满足；包工具拿不到写方法，
    无法经此通道篡改用户设置。
    """

    def get_shell_path(self) -> Optional[str]: ...
    def get_shell_command_prefix(self) -> Optional[str]: ...
    def get_image_auto_resize(self) -> bool: ...


@dataclass
class ToolContext:
    """包 LLM 工具的构造期上下文（不变量，唯一构造注入通道）。

    设计原则——"不变量给值，可变量给访问器"：
    - ``cwd``：会话级不变，直接给值；
    - ``settings``：会 reload，给活视图（读时取最新）。

    执行期的会话可变状态（当前模型等）不进本类——由 ``ToolExecContext``
    经 ``execute`` 第 5 参在每次调用时注入（对齐 pi ``wrapToolDefinition``
    的 ``ctxFactory``），天然拿到当前值，无需后绑定。
    """

    cwd: str
    settings: ToolSettingsView


@dataclass(frozen=True)
class ToolExecContext:
    """包 LLM 工具的执行期上下文（``execute`` 第 5 参）。

    每次工具调用由 ``context_provider`` 现造（冻结值对象），反映调用时刻
    的会话状态。将来工具需要的会话能力（发消息、读会话状态等）只在本类
    加字段，不开第二条注入通道。

    ``ui`` / ``has_ui``（pi ``ctx.ui`` / ``ctx.hasUI`` 对位）：执行期 UI
    句柄——工具执行中途可弹确认/选择（反向原语经 RPC 到前端，**Node 只
    渲染**，工具逻辑不出 Python）。注入点已织入两层纪律：run abort 竞速
    （Esc 时挂在半空的对话框被 ``ui/cancel`` 撤掉、按 cancelled 解决）与
    弹窗串行锁（并行工具调用的 UI 请求排队而非互踩）；两者由
    ``ScopedUIContext`` 承载，工具作者零 signal 代码。headless/无 UI
    前端时 ``has_ui=False``、``ui`` 为 NoOp（请求按空响应安全降级）——
    交互路径一律先判 ``has_ui`` 再决定走不走。

    ``agents``：会话 agents 注册表快照（``{注册名: AgentConfig}``，注入点
    现取 ``resource_loader.get_agents()``）——subagent 等委派类工具按名
    查表，工具侧不再自行发现（注册表即单一事实源，发现管线零重复）。
    """

    model: Optional[Model] = None
    ui: UIContext = _SHARED_NOOP_UI
    has_ui: bool = False
    agents: Dict[str, AgentConfig] = field(default_factory=dict)


ToolContextProvider = Callable[[], ToolExecContext]
"""执行期上下文工厂：每次工具调用时现取（对齐 pi ``ctxFactory``）。"""

NULL_TOOL_EXEC_CONTEXT = ToolExecContext()
"""无会话来源时的共享兜底执行期上下文（冻结对象，可安全共享）。"""


class _NullToolSettings:
    """无 settings 来源时的兜底只读视图（standalone loader / 测试用）。"""

    def get_shell_path(self) -> Optional[str]:
        return None

    def get_shell_command_prefix(self) -> Optional[str]:
        return None

    def get_image_auto_resize(self) -> bool:
        return True


NULL_TOOL_SETTINGS = _NullToolSettings()


@dataclass
class ToolDefinition:
    """Unified tool definition used by both extension tools and package-managed tools.

    执行体通过以下两种方式之一提供：
    - ``execute``: 直接的可调用对象（SDK 自定义工具或由 loader 绑定的包管理工具）。
      统一签名 ``(tool_call_id, params, signal, on_update, ctx)``，``ctx``
      为 ``ToolExecContext``（每次调用由 ``DynamicTool`` 经 context_provider 现取）。
    - ``executor_path``: 包管理工具所在的 ``executor.py`` 路径，由 ``ToolLoader`` 加载后填充 ``execute``。
    """

    name: str
    description: str
    parameters: dict = field(default_factory=dict)

    label: Optional[str] = None
    execution_mode: Optional[ToolExecutionMode] = None

    # 预留扩展点：工具自渲染回调（对齐 pi ToolDefinition 的 renderCall/
    # renderResult 概念）。富渲染归 Node 层（工具 details 平铺数据 + 前端
    # 按名渲染）；本字段面向将来的文本渲染面（print/headless 模式的逐工具
    # 定制）。当前无消费者，loader 不读这两个类属性——落地时再接线，
    # 勿提前消费。
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


__all__ = [
    "ToolInfo",
    "ToolDefinition",
    "ToolContext",
    "ToolExecContext",
    "ToolContextProvider",
    "ToolSettingsView",
    "NULL_TOOL_SETTINGS",
    "NULL_TOOL_EXEC_CONTEXT",
]
