"""用户工具（user tool）资源类型。

用户工具是用户/前端触发、执行结果以自定义消息类型记录并主动注入 LLM
上下文的宿主能力（对照 LLM 工具：模型 tool_call 触发、结果走工具消息）。

设计约束（见 ``examples/user_tools_design.md``）：泛化层只接管"管道"
（pending/flush、abort 级联、消息记录、RPC dispatch），不接管"能力"——
``parameters`` 与 ``on_event`` 事件通道对注册表不透明，各工具自行解释。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from nova_agent import CustomAgentMessage
from nova_ai.types.base_model import NovaBaseModel
from nova_harness.core.types.extensions.source import SourceInfo

# 进度事件回调签名：``on_event(event_name, data)``，同步/异步均可。
UserToolEventCallback = Callable[[str, Dict[str, Any]], Any]

# 执行体签名：``async execute(params, on_event, signal) -> CustomAgentMessage``
#
# - params：不透明参数字典（各工具自声明 JSON Schema，registry 只透传）；
# - on_event：进度事件回调，经 ``user_tool_event`` 透出到前端；
# - signal：取消信号（会话 abort 时级联触发）。
#
# 返回的消息实例由会话层统一处理：双写 agent state + 会话 JSONL，
# 流式期间挂 pending、turn 结束 flush。消息应实现
# ``nova_harness.core.types.messages.ContextInjectable`` 协议，
# 否则无法进入 LLM 上下文。
UserToolExecute = Callable[
    [Dict[str, Any], Optional[UserToolEventCallback], Any],
    Awaitable[CustomAgentMessage],
]

# 拦截结果 → 消息 转换器签名：``build_result_message(params, result)``。
#
# 扩展拦截事件（当前唯一：``user_bash``）返回完整 result 时，会话层跳过
# 真实执行，经本转换器把 result 翻译为本工具的消息形态记录——泛化层
# 不认识具体消息类型（如 BashExecutionMessage 由包分发），转换能力
# 只能由工具自身声明。
UserToolBuildResultMessage = Callable[[Dict[str, Any], Any], CustomAgentMessage]


class UserToolInfo(NovaBaseModel):
    """用户工具展示元数据（RPC catalog 的对外形态）。"""

    name: str
    description: str
    parameters: Optional[Dict[str, Any]] = None
    source: Optional[str] = None
    source_info: Optional["SourceInfo"] = None


@dataclass
class UserToolDefinition:
    """用户工具定义。

    执行体通过 ``execute`` 可调用对象提供；包级用户工具由 loader 从包内
    ``executor.py`` 装载工厂、在会话创建时绑定会话上下文生成本定义。
    """

    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)

    # 执行体
    execute: Optional[UserToolExecute] = None

    # 拦截结果转换器（可选）：扩展拦截事件返回完整 result 时，
    # 会话层用它把 result 翻译为本工具的消息形态记录（loader 从
    # UserTool 实例的可选 ``message_from_result`` 方法绑定）
    build_result_message: Optional[UserToolBuildResultMessage] = None

    # 来源信息
    source_info: Optional["SourceInfo"] = None


# 包级用户工具工厂签名：``create(session) -> UserToolDefinition``。
# 会话上下文由工厂闭包捕获（settings、cwd、扩展 spawn hooks 等执行期读取）。
UserToolFactory = Callable[[Any], UserToolDefinition]


@dataclass
class UserToolResource:
    """包级用户工具的加载产物：展示元数据 + 绑定会话的工厂。

    与 ``UserToolDefinition`` 的区别：definition 是绑定会话后的可执行
    形态（每会话一份）；resource 是 loader 从包目录装载的会话无关形态
    （每进程一份），会话创建时经 ``create(session)`` 实例化。
    """

    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    create: Optional[UserToolFactory] = None
    source_info: Optional["SourceInfo"] = None


__all__ = [
    "UserToolEventCallback",
    "UserToolExecute",
    "UserToolBuildResultMessage",
    "UserToolDefinition",
    "UserToolFactory",
    "UserToolInfo",
    "UserToolResource",
]
