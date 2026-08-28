"""线上 item 层类型（呈现层实体形状）。

item 是归约层（``core/harness/reduction/``）的线上载体：会话的实时事件流
与恢复读在**服务器侧**归约为统一的 item 序列（``item/started|delta|completed``
通知），客户端只做 apply 不做归约。

- **框架变体**（本文件六个）：LLM 会话的固有形状——用户消息/助手消息/思考/
  工具调用/压缩/分支摘要，协议内置（``FrameworkItem`` 判别联合）；
- **包级变体**：包定义 ``NovaItem`` 子类（如 nova-coding-agent 的
  ``BashExecutionItem``）随包注册声明；静态契约无法枚举包类型，线上联合
  以 ``CustomItem`` 兜底（``type`` 开放字符串 + 额外字段透传，前端
  ``entry:<type>`` 槽消费）。

落盘仍是消息形（JSONL，LLM 上下文热路径零转换）；item 是纯"线上/呈现层"
实体——翻译只发生在"LLM 形 ↔ 呈现形"之间、且只在服务器侧。
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, List, Literal, Optional, Union

from nova_ai.types.base_model import NovaBaseModel
from pydantic import ConfigDict, Field

from nova_harness.core.types.messages import CustomMessageContent


class ItemStatus(str, Enum):
    """item 生命周期状态（线上取 value）。

    - ``PENDING``：已建未启动（如待审批的调用）；
    - ``RUNNING``：进行中（流式/执行中）；
    - ``DONE`` / ``FAILED``：正常终结两态；
    - ``DECLINED``：被拒绝（审批否决）；
    - ``CANCELLED``：中断定稿（run abort 时在飞 item 的终结语义）。
    """

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    DECLINED = "declined"
    CANCELLED = "cancelled"


class NovaItem(NovaBaseModel):
    """item 基类（判别字段 ``type``，子类以 Literal 收窄）。"""

    id: str = ""
    type: str = ""
    status: Optional[ItemStatus] = None
    # 来源分源：约定 "agent"（LLM 产出）/ "user"（用户直接动作，如 !cmd）；
    # 包级变体可扩展取值，故保持开放字符串
    source: Optional[str] = None
    ts: int = 0  # 创建时刻（epoch ms）


# ---------------------------------------------------------------------------
# 框架变体
# ---------------------------------------------------------------------------


class UserMessageItem(NovaItem):
    """用户消息。"""

    type: Literal["userMessage"] = "userMessage"
    content: List[CustomMessageContent] = Field(default_factory=list)


class AgentMessageItem(NovaItem):
    """助手文本消息（流式：started 建 → delta 追加 → completed 定稿）。"""

    type: Literal["agentMessage"] = "agentMessage"
    text: str = ""
    # 失败定稿时的错误文本（message.error_message——供应商/网络错误
    # 的用户可见呈现；cancelled 不用它，前端按 status 自组文案）
    error: Optional[str] = None


class ThinkingItem(NovaItem):
    """推理/思考内容（同为流式）。"""

    type: Literal["thinking"] = "thinking"
    text: str = ""


class ToolCallItem(NovaItem):
    """LLM 工具调用（一等实体）。

    ``args``/``result`` 与工具事件同型（``Any``）——参数与结果的具体形状
    归工具所属的包，框架不约束。
    """

    type: Literal["toolCall"] = "toolCall"
    tool: str = ""
    args: Any = None
    # 参数已完整（pending 期内的时点标记：assistant message_end 时置位）——
    # "参数完整、执行未开始"窗口的标记：edit 类工具的执行前只读预览
    # （diff 计算）在这个时点触发（pi setArgsComplete 对位）
    args_complete: bool = False
    result: Any = None
    # 执行中的流式部分结果（tool_execution_update 的 transient 载体——
    # 渲染器据此实时更新卡片；completed 定稿时清空，不落盘）
    partial_result: Any = None
    duration_ms: Optional[int] = None
    error: Optional[str] = None


class CompactionItem(NovaItem):
    """上下文压缩标记。"""

    type: Literal["compaction"] = "compaction"
    summary: str = ""
    tokens_before: int = 0
    # 触发原因（auto/manual 等），由压缩域填充
    reason: Optional[str] = None


class BranchSummaryItem(NovaItem):
    """分支摘要（会话树导航回返时注入的摘要）。"""

    type: Literal["branchSummary"] = "branchSummary"
    summary: str = ""
    from_id: str = ""


# 框架变体判别联合（协议内置的封闭集——LLM 会话固有形状）
FrameworkItem = Annotated[
    Union[
        UserMessageItem,
        AgentMessageItem,
        ThinkingItem,
        ToolCallItem,
        CompactionItem,
        BranchSummaryItem,
    ],
    Field(discriminator="type"),
]


class CustomItem(NovaItem):
    """包级 item 变体的线上兜底形态。

    包定义自己的 ``NovaItem`` 子类（强类型字段）随包注册；静态契约
    （JSON Schema / TS）无法枚举包类型，线上联合以本形态兜底——
    ``type`` 为开放字符串、额外字段原样透传。
    """

    model_config = ConfigDict(extra="allow")

    type: str = "custom"
    details: Any = None


# 线上联合 = 框架变体 + 包级兜底（契约导出根；运行时包变体以自身类 dump，
# 线上形状与 CustomItem 相容）
NovaWireItem = Union[
    UserMessageItem,
    AgentMessageItem,
    ThinkingItem,
    ToolCallItem,
    CompactionItem,
    BranchSummaryItem,
    CustomItem,
]

# 校验联合（model_validate 方向——RPC 出参归一等"dict → 模型"路径用）：
# 框架变体按 ``type`` 精确判别，包级 dict 落 CustomItem 兜底（extra 透传），
# 包级**实例**（如 BashExecutionItem）落 NovaItem 基类成员原样保留（实例
# 不重校验，dump 时经 SerializeAsAny 按实际类型出全字段）。
# SerializeAsAny 只管序列化方向；校验侧若不显式可判别，会按基类重建、
# 子类字段被剥（syncSession 出参归一曾因此剥掉 text 等字段）。
# left_to_right：先试 FrameworkItem（tagged 精确），再 CustomItem，最后
# 基类——消除 smart union 的评分不确定性。
WireItem = Annotated[
    Union[FrameworkItem, CustomItem, NovaItem],
    Field(union_mode="left_to_right"),
]


__all__ = [
    "ItemStatus",
    "NovaItem",
    "UserMessageItem",
    "AgentMessageItem",
    "ThinkingItem",
    "ToolCallItem",
    "CompactionItem",
    "BranchSummaryItem",
    "CustomItem",
    "FrameworkItem",
    "NovaWireItem",
    "WireItem",
]
