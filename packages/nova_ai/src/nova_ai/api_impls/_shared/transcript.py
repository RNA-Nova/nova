"""转录折叠机（逐函数对齐 pi ``utils/transcript.ts``）。

系统提示词与工具声明**由转录中的 system 消息承载**：``normalize_context``
把 ``Context.system_prompt``/``Context.tools`` 折成首条 ``SystemMessage``
（pi ``normalizeContext`` 对位），此后协议层只在消息列表上工作：

- ``resolve_transcript``：总闸门——模型接受对话中段 system 消息
  （``supports_mid_convo_system_messages`` 旗标）则原样下发；
  否则 ``collapse_system_messages``——全部 system 消息折叠成一条放开头。
- 折叠（``get_current_*``）：toolsAdded/toolsRemoved 按序应用出当前工具集；
  content 追加、sections 按名打补丁（``None`` 删除）出当前提示词。
"""

from __future__ import annotations

import copy
from typing import Dict, List, Literal, NamedTuple, Optional, Sequence, TypeGuard

from nova_protocol import Context, Message, SystemMessage, Tool, ToolReference


def _content_text(content: object) -> str:
    """content（str 或 TextContent 列表）→ 文本（pi ``contentText``）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.text for block in content if getattr(block, "type", None) == "text"
        )
    return ""


def create_initial_system_message(
    system_prompt: Optional[str],
    tools: Optional[Sequence[Tool]],
) -> Optional[SystemMessage]:
    """构造首条 system 消息（pi ``createInitialSystemMessage``）：提示词
    与工具都空时返回 ``None``（空转录保持空）。"""
    has_prompt = bool(system_prompt)
    has_tools = bool(tools)
    if not has_prompt and not has_tools:
        return None
    return SystemMessage(
        content=system_prompt or "",
        tools_added=list(tools) if has_tools else None,
        timestamp=0,
    )


def normalize_context(context: Context) -> List[Message]:
    """``Context.system_prompt``/``Context.tools`` 折成首条 system 消息
    （pi ``normalizeContext``）——返回纯消息列表视图。"""
    initial = create_initial_system_message(context.system_prompt, context.tools)
    if initial is not None:
        return [initial, *context.messages]
    return list(context.messages)


def _is_system(message: Message) -> TypeGuard[SystemMessage]:
    return getattr(message, "role", None) == "system"


def get_initial_system_message(messages: Sequence[Message]) -> Optional[SystemMessage]:
    """取首条 system 消息（pi ``getInitialSystemMessage``）。"""
    first = messages[0] if messages else None
    return first if first is not None and _is_system(first) else None


def without_initial_system_message(messages: Sequence[Message]) -> List[Message]:
    """丢掉首条 system 消息（pi ``withoutInitialSystemMessage``——system
    走顶层参数的协议用，如 Anthropic）。"""
    if get_initial_system_message(messages) is not None:
        return list(messages[1:])
    return list(messages)


def get_current_tools(messages: Sequence[Message]) -> List[Tool]:
    """按序应用 toolsAdded/toolsRemoved → 当前工具集（pi ``getCurrentTools``）。"""
    tools: Dict[str, Tool] = {}
    for message in messages:
        if not _is_system(message):
            continue
        for ref in getattr(message, "tools_removed", None) or []:
            tools.pop(ref.name, None)
        for tool in getattr(message, "tools_added", None) or []:
            tools[tool.name] = tool
    return list(tools.values())


def get_current_system_message(messages: Sequence[Message]) -> Optional[SystemMessage]:
    """折叠全部 system 消息为一条（pi ``getCurrentSystemMessage``）：
    content 逐条追加、sections 按名打补丁（``None`` 删除）、工具集折叠。"""
    contents: List[str] = []
    sections: Dict[str, Optional[str]] = {}
    timestamp: Optional[int] = None
    for message in messages:
        if not _is_system(message):
            continue
        if timestamp is None:
            timestamp = message.timestamp
        text = _content_text(message.content)
        if text:
            contents.append(text)
        for name, value in (message.sections or {}).items():
            if value is None:
                sections.pop(name, None)
            else:
                sections[name] = value
    tools = get_current_tools(messages)
    if timestamp is None and not tools:
        return None
    return SystemMessage(
        content="\n\n".join(contents),
        sections=sections or None,
        tools_added=tools or None,
        timestamp=timestamp or 0,
    )


# ---------------------------------------------------------------------------
# 工具声明的传输分工（pi resolveTranscriptTools）
# ---------------------------------------------------------------------------

TranscriptToolSupport = Literal["none", "additions", "changes"]
"""传输表达工具变更的能力档（pi ``TranscriptToolSupport``）：

- ``"none"``：只有顶层工具列表——恒为当前工具集；
- ``"additions"``：后续消息可新增工具，但不可移除/重定义（Kimi K3）；
- ``"changes"``：每条 system 消息（含首条）就地声明工具，同名后声明
  替换先声明（Anthropic INLINE_TOOLS）；移除在线表达不了，由文本宣告。
"""


class TranscriptTools(NamedTuple):
    """工具声明的顶层/就地分工（pi ``TranscriptTools``）。"""

    request_tools: List[Tool]
    """顶层请求字段携带的工具。"""
    folded_system_messages: int
    """前多少条 system 消息的工具变更已含在 ``request_tools`` 里——
    之后的 system 消息各自就地携带变更。"""


def _find_last_non_additive_tool_change(system_messages: Sequence[Message]) -> int:
    """最后一条含移除或同名重声明的 system 消息序号（无则 -1）。

    已声明过的名字再声明即重定义——即使中途被移除过也算（Kimi K3 会
    忽略"移除后又在工具消息里声明"的工具，pi 2026-10 实测）。
    """
    declared: set = set()
    last = -1
    for index, message in enumerate(system_messages):
        if getattr(message, "tools_removed", None):
            last = index
        for tool in getattr(message, "tools_added", None) or []:
            if tool.name in declared:
                last = index
            declared.add(tool.name)
    return last


def resolve_transcript_tools(
    messages: Sequence[Message], support: TranscriptToolSupport
) -> TranscriptTools:
    """顶层请求字段与就地变更之间的工具声明分工（pi ``resolveTranscriptTools``）。

    - ``"changes"``：顶层留空，每条 system 消息（含首条）就地声明——
      就地声明能替换先前的就地声明，替换不了顶层声明；
    - ``"additions"``：移除/重定义无法就地表达，顶层列表取**最后一次
      非增量变更之后**的工具状态（缓存前缀每次移除只失效一次，而不是
      每次后续新增都失效）；后续新增就地锚定；
    - ``"none"``：顶层即当前工具集。
    """
    system_messages = [m for m in messages if _is_system(m)]
    if support == "none":
        return TranscriptTools(
            request_tools=get_current_tools(system_messages),
            folded_system_messages=len(system_messages),
        )
    if support == "changes":
        return TranscriptTools(request_tools=[], folded_system_messages=0)
    folded = max(
        1 if get_initial_system_message(messages) is not None else 0,
        _find_last_non_additive_tool_change(system_messages) + 1,
    )
    return TranscriptTools(
        request_tools=get_current_tools(system_messages[:folded]),
        folded_system_messages=folded,
    )


def get_system_message_text(message: SystemMessage) -> str:
    """system 消息 → 完整提示词文本（pi ``getSystemMessageText``：
    content 随后跟各 section 值）。"""
    parts = [_content_text(message.content)]
    for text in (message.sections or {}).values():
        if text is not None:
            parts.append(text)
    return "\n\n".join(part for part in parts if part)


def render_system_message_update(message: SystemMessage) -> str:
    """中途 system 消息的呈现文本（pi ``renderSystemMessageUpdate``）：
    段变更以名字表述，模型可与开头提示词对上号。"""
    parts: List[str] = []
    text = _content_text(message.content)
    if text:
        parts.append(text)
    for name, value in (message.sections or {}).items():
        if value is None:
            parts.append(f'Removed system prompt section "{name}".')
        else:
            parts.append(f'Updated system prompt section "{name}":\n\n{value}')
    return "\n\n".join(parts)


def collapse_system_messages(messages: Sequence[Message]) -> List[Message]:
    """不支持中途 system 消息的形态（pi ``collapseSystemMessages``）：
    折叠的一条放开头，其余 system 消息丢弃。"""
    head = get_current_system_message(messages)
    rest = [m for m in messages if not _is_system(m)]
    return ([head] if head is not None else []) + rest


def resolve_transcript(
    messages: Sequence[Message],
    supports_mid_convo_system_messages: Optional[bool],
) -> List[Message]:
    """总闸门（pi ``resolveTranscript``）：模型接受对话中段的 system
    消息则转录原样下发，否则折叠。"""
    if supports_mid_convo_system_messages:
        return list(messages)
    return collapse_system_messages(messages)


def get_current_system_prompt(messages: Sequence[Message]) -> str:
    """重放全部 system 消息，返回当前提示词文本。"""
    message = get_current_system_message(messages)
    return get_system_message_text(message) if message is not None else ""


def to_tool_declaration(tool: Tool) -> Tool:
    """剥去执行/展示字段，得到用于转录与比较的最小工具声明。"""
    return Tool(
        name=tool.name,
        description=tool.description,
        parameters=copy.deepcopy(tool.parameters),
        constrained_sampling=tool.constrained_sampling,
    )


def declarations_equal(left: Tool, right: Tool) -> bool:
    """两个工具是否具有相同的模型可见声明。"""
    return (
        to_tool_declaration(left).model_dump()
        == to_tool_declaration(right).model_dump()
    )


class ToolStateChanges(NamedTuple):
    """两组完整工具状态之间的新增与移除（定义变化 = 先删后增）。"""

    tools_added: List[Tool]
    tools_removed: List[ToolReference]


def get_tool_state_changes(
    previous: Sequence[Tool], current: Sequence[Tool]
) -> ToolStateChanges:
    """比较完整工具状态，产出可重放的新增/删除集。"""
    previous_tools = {tool.name: tool for tool in previous}
    current_tools = {tool.name: tool for tool in current}
    return ToolStateChanges(
        tools_added=[
            to_tool_declaration(tool)
            for tool in current
            if tool.name not in previous_tools
            or not declarations_equal(previous_tools[tool.name], tool)
        ],
        tools_removed=[
            ToolReference(name=tool.name)
            for tool in previous
            if tool.name not in current_tools
            or not declarations_equal(tool, current_tools[tool.name])
        ],
    )


__all__ = [
    "ToolStateChanges",
    "TranscriptTools",
    "TranscriptToolSupport",
    "collapse_system_messages",
    "create_initial_system_message",
    "declarations_equal",
    "get_current_system_message",
    "get_current_system_prompt",
    "get_current_tools",
    "get_initial_system_message",
    "get_system_message_text",
    "get_tool_state_changes",
    "normalize_context",
    "render_system_message_update",
    "resolve_transcript",
    "resolve_transcript_tools",
    "to_tool_declaration",
    "without_initial_system_message",
]
