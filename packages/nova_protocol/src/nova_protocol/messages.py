"""
核心消息类型定义
"""

from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import ConfigDict, Field

from .base_model import NovaBaseModel
from .content import ImageContent, TextContent, ThinkingContent, ToolCall
from .enums import Api, ImagesStopReason, NovaErrorKind, ProviderId, StopReason
from .model import Usage


class UserMessage(NovaBaseModel):
    """用户消息（用于上下文理解）"""

    role: Literal["user"] = "user"
    # 内嵌联合同规则 6：显式判别（type 键），不靠 smart-union 猜
    content: Union[
        str,
        List[Annotated[Union[TextContent, ImageContent], Field(discriminator="type")]],
    ] = Field(
        default_factory=list,
    )
    timestamp: int = 0


class AssistantMessage(NovaBaseModel):
    """助手消息。

    双重身份（AGENTS.md 数据建模规则 1 注记）：既是消息契约（规则 2 低频
    契约对象——随会话 JSONL / RPC 落盘出网），又是流式累积器（``_stream.py``
    在流式循环内原地改写 usage / stop_reason / content 等）——表示保留
    Pydantic 以满足边界 parse/dump 与规则 6 判别；赋值期校验关闭（校验发生
    在构造与解析边界，不跟每次增量写）。
    """

    model_config = ConfigDict(validate_assignment=False)

    role: Literal["assistant"] = "assistant"
    content: List[
        Annotated[
            Union[TextContent, ThinkingContent, ToolCall], Field(discriminator="type")
        ]
    ] = Field(default_factory=list)
    api: Api = "unknown"
    provider: ProviderId = "unknown"
    model: str = "unknown"
    usage: Usage = Field(default_factory=Usage)
    stop_reason: StopReason = StopReason.STOP
    # 提供商原始 finish_reason（未映射；对齐 TS rawStopReason）；None = 未上报
    raw_stop_reason: Optional[str] = None
    # 提供商标记的回合自然结束（对齐 TS endTurn）；None = 提供商无此概念
    end_turn: Optional[bool] = None
    # 提供商延迟完成标记（对齐 TS deferred）；None = 非延迟完成
    deferred: Optional[bool] = None
    # 错误详情（stop_reason == error 时填充）；None = 无错误
    error_message: Optional[str] = None
    # 错误分类（stop_reason == error 时由归一单点填充，docs/crosscutting/diagnostics-modeling.md）；
    # None = 无错误或未分类（旧数据）
    error_kind: Optional[NovaErrorKind] = None
    # 服务端要求的退避毫秒数（429 的 Retry-After 族，归一单点填充）；
    # None = 服务端未给指引——消费方（自动重试）回退本地指数退避
    retry_after_ms: Optional[int] = None
    # 提供商响应级 id / 实际服务模型（与请求 model 不同说明被网关改写）；None = 未上报
    response_id: Optional[str] = None
    response_model: Optional[str] = None
    # 端点原生 effort 级（adaptive thinking 的生效档位——对齐 TS
    # ``providerThinkingLevel``）：mid-convo effort 开启时记录在回复上，
    # 后续请求经 effort 标记回放；``None`` = 未记录
    provider_thinking_level: Optional[str] = None
    # 脱敏后的 provider/运行时诊断（失败与恢复路径，开放形状列表）；None = 无诊断
    diagnostics: Optional[List[Dict[str, Any]]] = None
    timestamp: int = 0
    # 本流所见响应的毫秒耗时（monotonic 计量）；None = 旧消息或延迟结果。
    duration_ms: Optional[int] = None


class ToolResultMessage(NovaBaseModel):
    """工具结果消息（用于上下文理解）"""

    role: Literal["toolResult"] = "toolResult"
    tool_call_id: str = ""
    tool_name: str = ""
    content: List[
        Annotated[Union[TextContent, ImageContent], Field(discriminator="type")]
    ] = Field(default_factory=list)
    # 任意 JSON 值（对齐 TS details: unknown）——工具自定义形态，渲染端按工具解释
    details: Optional[Any] = None
    is_error: bool = False
    timestamp: int = 0


class ConstrainedSampling(NovaBaseModel):
    """工具级约束采样声明（对位 pi ``Tool.constrainedSampling``）。

    ``strict="prefer"``：schema 与端点 strict 子集兼容时启用 strict，不兼容
    自动回落非严格；``"require"``：不兼容即请求期报错（不给模型次优）。
    grammar 族（OpenAI lark/regex）随 OpenAI provider 落地再扩。
    """

    type: Literal["json_schema"]
    strict: Literal["prefer", "require"]


class Tool(NovaBaseModel):
    """工具定义（发给模型的 function spec）。

    ``parameters`` 是 JSON Schema 载体：线上即自由 dict（各家 provider
    还有方言差异），本层原样运载、不理解、不校验（``convert_tools``
    直通请求体）。schema 的创作源与调用参数的校验归工具所在包
    （params_model 派生），不进本层。
    """

    name: str
    description: str
    parameters: Dict[str, Any]
    # 工具级约束采样声明（``None`` = 不参与 strict——对位 pi 缺省）
    constrained_sampling: Optional[ConstrainedSampling] = None


class ToolReference(NovaBaseModel):
    """工具引用（按名——对齐 pi ``ToolReference``）。"""

    name: str


class SystemMessage(NovaBaseModel):
    """系统消息（转录中的提示词/工具变更事件——pi ``SystemMessage`` 对位）。

    不再是"开头的固定起始条件"：``content`` 自该点起追加指令；
    ``sections`` 按名替换提示词块（``None`` 值删除该块）；
    ``tools_added``/``tools_removed`` 变更工具集。按序重放全部 system
    消息即得当前提示词与工具集（折叠）；不支持中途 system 消息的协议
    经折叠形态下发。
    """

    role: Literal["system"] = "system"
    # 指令文本（首条为基础提示词，后续为追加指令）
    content: Union[str, List[TextContent]] = ""
    # 命名提示词块（后写覆盖先写，None 值删除）
    sections: Optional[Dict[str, Optional[str]]] = None
    # 该点起新增可用的工具（完整定义）
    tools_added: Optional[List[Tool]] = None
    # 该点起停用的工具（按名）
    tools_removed: Optional[List[ToolReference]] = None
    timestamp: int = 0


# 消息联合类型（判别键 ``role``——规则 6：Context.messages 走 model_validate）
Message = Annotated[
    Union[UserMessage, AssistantMessage, ToolResultMessage, SystemMessage],
    Field(discriminator="role"),
]
"""上下文消息的 role 判别联合。"""


class Context(NovaBaseModel):
    """上下文。

    ``tools``：本轮可用的工具定义；``None`` = 无工具（与空列表同义，
    序列化保留原样不归一）。
    """

    system_prompt: Optional[str] = None
    messages: List[Message] = Field(default_factory=list)
    tools: Optional[List[Tool]] = None


class ImagesContext(NovaBaseModel):
    """一次图像生成的输入；只含文本与 base64 图像块，不是聊天转录。"""

    input: List[
        Annotated[Union[TextContent, ImageContent], Field(discriminator="type")]
    ]


class AssistantImages(NovaBaseModel):
    """非流式图像生成结果（pi ``AssistantImages``）。

    ``usage``/``response_id`` 为 None = provider 未上报；``error_message``
    只在 error/aborted 终态携带诊断原文。图像数据仍在 ImageContent.data。
    """

    api: Api
    provider: ProviderId
    model: str
    output: List[
        Annotated[Union[TextContent, ImageContent], Field(discriminator="type")]
    ] = Field(default_factory=list)
    response_id: Optional[str] = None
    usage: Optional[Usage] = None
    stop_reason: ImagesStopReason = ImagesStopReason.STOP
    error_message: Optional[str] = None
    timestamp: int = 0
