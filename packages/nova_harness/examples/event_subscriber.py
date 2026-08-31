from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from nova_agent import AgentEvent
from nova_agent.types.events import (
    AgentEndEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from nova_ai.types.content import ImageContent, TextContent, ThinkingContent, ToolCall
from nova_ai.types.messages import AssistantMessage, ToolResultMessage, UserMessage


def on_print(event: AgentEvent, file: TextIO = sys.stdout) -> None:
    """默认的 Agent 事件订阅函数：将事件以人类可读的格式输出到指定流。

    该函数主要用于调试与示例。为避免打印失败导致 agent loop 中断，
    所有异常都被捕获并仅输出一条错误提示。

    Args:
        event: nova_agent 发出的事件。
        file: 输出目标，默认为 ``sys.stdout``。
    """
    try:
        _dispatch(event, file)
    except Exception as exc:  # pragma: no cover - 防御性处理
        file.write(f"\n[event_subscriber 输出错误: {exc}]\n")
        file.flush()


def _dispatch(event: AgentEvent, file: TextIO) -> None:
    if isinstance(event, AgentStartEvent):
        file.write("\n=== Agent 启动 ===\n")
        file.flush()

    elif isinstance(event, AgentEndEvent):
        messages = event.messages or []
        file.write(f"\n=== Agent 结束 (共 {len(messages)} 条消息) ===\n\n")
        file.flush()

    elif isinstance(event, TurnStartEvent):
        file.write("\n--- 新回合开始 ---\n")
        file.flush()

    elif isinstance(event, TurnEndEvent):
        results = event.tool_results or []
        file.write(f"--- 回合结束 (工具结果数: {len(results)}) ---\n\n")
        file.flush()

    elif isinstance(event, MessageStartEvent):
        message = event.message
        role = getattr(message, "role", "unknown")
        file.write(f"\n[{role}] ")
        file.flush()

    elif isinstance(event, MessageUpdateEvent):
        _print_message_update(event, file)

    elif isinstance(event, MessageEndEvent):
        _print_message_end(event, file)

    elif isinstance(event, ToolExecutionStartEvent):
        name = event.tool_name or "unknown"
        args = event.args
        args_str = _serialize(args) if args is not None else ""
        file.write(f"\n[工具开始] {name}({args_str})\n")
        file.flush()

    elif isinstance(event, ToolExecutionUpdateEvent):
        result = event.partial_result
        if result is not None:
            file.write(f"  [工具更新] {_serialize(result)}\n")
            file.flush()

    elif isinstance(event, ToolExecutionEndEvent):
        name = event.tool_name or "unknown"
        status = "✓ 成功" if not event.is_error else "✗ 失败"
        file.write(f"[工具结束] {name} {status}\n")
        if event.result is not None:
            file.write(f"  结果: {_serialize(event.result)}\n")
            file.flush()

    else:
        # 兜底：遇到未识别的事件类型时仅输出类型名
        file.write(f"\n[未知事件] {getattr(event, 'type', '?')}\n")
        file.flush()


def _print_message_update(event: MessageUpdateEvent, file: TextIO) -> None:
    """处理流式消息更新事件，将增量内容实时写出。"""
    ame = event.assistant_message_event
    if ame is None:
        return

    event_type = ame.type

    if event_type == "text_start":
        # 文本块开始，无需额外换行，增量会直接追加
        pass
    elif event_type == "text_delta":
        file.write(ame.delta)
        file.flush()
    elif event_type == "text_end":
        file.write("\n")
        file.flush()

    elif event_type == "thinking_start":
        file.write("\n[thinking] ")
        file.flush()
    elif event_type == "thinking_delta":
        file.write(ame.delta)
        file.flush()
    elif event_type == "thinking_end":
        file.write("\n")
        file.flush()

    elif event_type == "toolcall_start":
        file.write("\n[tool_call] ")
        file.flush()
    elif event_type == "toolcall_delta":
        file.write(ame.delta)
        file.flush()
    elif event_type == "toolcall_end":
        file.write("\n")
        file.flush()

    # "start"/"done"/"error" 等事件由外层 MessageStartEvent/MessageEndEvent 覆盖，
    # 这里不需要重复输出。


def _print_message_end(event: MessageEndEvent, file: TextIO) -> None:
    """处理消息结束事件，输出最终内容、工具调用、思考过程与错误信息。"""
    message = event.message
    if message is None:
        file.write("\n")
        file.flush()
        return

    role = getattr(message, "role", "unknown")

    if isinstance(message, AssistantMessage):
        for content in message.content or []:
            if isinstance(content, TextContent):
                if content.text:
                    file.write(f"\n[answer]: {content.text}\n")
            elif isinstance(content, ThinkingContent):
                if content.thinking:
                    label = "[thinking]"
                    if content.redacted:
                        label = "[thinking (redacted)]"
                    file.write(f"\n{label}: {content.thinking}\n")
            elif isinstance(content, ToolCall):
                args_str = _serialize(content.arguments)
                file.write(f"\n[tool_call]: {content.name}({args_str})\n")

        if message.error_message:
            file.write(f"\n[error]: {message.error_message}\n")
        if message.model:
            file.write(f"[model]: {message.model}\n")

        file.write(f"\n[消息完成] {role}\n")
        file.flush()

    elif isinstance(message, UserMessage):
        text = _extract_user_message_text(message)
        file.write(f"{text}\n[消息完成] {role}\n")
        file.flush()

    elif isinstance(message, ToolResultMessage):
        file.write(f"\n[tool_result]: {message.tool_name}\n")
        if message.is_error:
            file.write("  status: error\n")
        content_text = _extract_content_text(message.content)
        if content_text:
            file.write(f"  {content_text}\n")
        file.write(f"[消息完成] {role}\n")
        file.flush()

    else:
        file.write(f"\n[消息完成] {role}\n")
        file.flush()


def _extract_user_message_text(message: UserMessage) -> str:
    """从用户消息中提取可阅读的文本摘要。"""
    content = message.content
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts: list[str] = []
    for item in content:
        if isinstance(item, TextContent):
            parts.append(item.text)
        elif isinstance(item, ImageContent):
            parts.append("[image]")
        else:
            parts.append(str(item))
    return "".join(parts)


def _extract_content_text(content: Any) -> str:
    """从内容块列表中提取文本，避免打印 base64 图像等过长内容。"""
    if not isinstance(content, list):
        return str(content)

    parts: list[str] = []
    for item in content:
        if isinstance(item, TextContent):
            parts.append(item.text)
        elif isinstance(item, ImageContent):
            parts.append("[image]")
        else:
            parts.append(str(item))
    return "".join(parts)


def _serialize(value: Any) -> str:
    """将参数/结果序列化为紧凑 JSON；失败时回退到 str()。"""
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        return str(value)


__all__ = ["on_print"]
