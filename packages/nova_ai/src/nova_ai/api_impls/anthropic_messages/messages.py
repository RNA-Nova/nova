"""消息与工具转换（对齐 TS ``convertMessages`` / ``convertTools`` /
``normalizeToolCallId`` / ``splitDeferredTools``）。

Anthropic 消息形态要点：

- system 是顶层参数不是消息（归 params.py）；
- assistant 内容块保真回放：text / thinking（**签名原样回传**，缺签名降级
  纯文本——``allow_empty_signature`` 的模型除外）/ redacted_thinking /
  tool_use；
- toolResult 消息合并为 user 消息的 tool_result 块；携带 ``added_tool_names``
  的条目额外发 tool_reference 块（deferred tools 装载，端点要求
  tool_reference 不得与普通 tool_result 内容混合——拆为同级兄弟块）；
- cache_control 打在**最后一条 user 消息的最后一个块**上（缓存会话历史）。
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from nova_protocol import (
    Context,
    ImageContent,
    Message,
    Model,
    TextContent,
    Tool,
    ToolResultMessage,
)

from ...utils.surrogate import sanitize_surrogates
from .._shared.prompt_cache import CacheControl
from .._shared.transform_messages import transform_messages
from .compat import ResolvedAnthropicCompat

# Anthropic 工具调用 id 的合法形状（对齐 TS normalizeToolCallId）
_TOOL_CALL_ID_PATTERN = re.compile(r"[^a-zA-Z0-9_-]")


def normalize_tool_call_id(tool_call_id: str) -> str:
    """规范化工具调用 id（非法字符换下划线，截断到 64——对齐 TS）。"""
    return _TOOL_CALL_ID_PATTERN.sub("_", tool_call_id)[:64]


def split_deferred_tools(
    context: Context, enabled: bool
) -> Tuple[List[Tool], Dict[str, Tool]]:
    """拆分当前工具为即刻/延迟两组（对齐 TS splitDeferredTools）。

    延迟组 = 经 toolResult 的 ``added_tool_names`` 引入且尚未被 toolCall
    使用的工具——随 ``defer_loading`` 下发，由 tool_reference 块按需装载。
    """
    unique: Dict[str, Tool] = {}
    for tool in context.tools or []:
        unique[tool.name] = tool
    if not enabled:
        return list(unique.values()), {}

    deferred_names: Set[str] = set()
    used_names: Set[str] = set()
    for message in context.messages:
        if message.role == "assistant":
            for block in message.content:
                if block.type == "toolCall":
                    used_names.add(block.name)
        elif isinstance(message, ToolResultMessage):
            for name in message.added_tool_names or []:
                if name not in used_names:
                    deferred_names.add(name)

    immediate: List[Tool] = []
    deferred: Dict[str, Tool] = {}
    for name, tool in unique.items():
        if name in deferred_names:
            deferred[name] = tool
        else:
            immediate.append(tool)
    return immediate, deferred


def _convert_content_blocks(content: List[Any]) -> Any:
    """TextContent/ImageContent 列表 → Anthropic 内容（纯文本时归并为字符串，
    对齐 TS convertContentBlocks；纯图片补占位文本块）。"""
    has_images = any(c.type == "image" for c in content)
    if not has_images:
        return sanitize_surrogates("\n".join(c.text for c in content))

    blocks: List[Dict[str, Any]] = []
    for block in content:
        if block.type == "text":
            blocks.append({"type": "text", "text": sanitize_surrogates(block.text)})
        else:
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": block.mime_type,
                        "data": block.data,
                    },
                }
            )
    if not any(b["type"] == "text" for b in blocks):
        blocks.insert(0, {"type": "text", "text": "(see attached image)"})
    return blocks


def _convert_tool_result(
    message: ToolResultMessage,
    deferred_tool_names: Set[str],
    loaded_tool_names: Set[str],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """toolResult → (tool_result 块, 兄弟内容块)（对齐 TS convertToolResult）。

    携带新增工具名的条目发 tool_reference 块（端点要求 tool_reference 不与普通
    tool_result 内容混合——原内容降级为兄弟块）。
    """
    references: List[Dict[str, Any]] = []
    for name in message.added_tool_names or []:
        if name not in deferred_tool_names or name in loaded_tool_names:
            continue
        loaded_tool_names.add(name)
        references.append({"type": "tool_reference", "tool_name": name})

    converted_content = _convert_content_blocks(message.content)
    tool_result = {
        "type": "tool_result",
        "tool_use_id": message.tool_call_id,
        "content": references if references else converted_content,
        "is_error": message.is_error,
    }
    if not references:
        return tool_result, []
    siblings = (
        [{"type": "text", "text": converted_content}]
        if isinstance(converted_content, str)
        else converted_content
    )
    return tool_result, siblings


def convert_messages(
    model: Model,
    context: Context,
    compat: ResolvedAnthropicCompat,
    cache_control: Optional[CacheControl],
    deferred_tool_names: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """把内部消息列表转换为 Anthropic MessageParam 形状（对齐 TS convertMessages）。"""
    transformed = transform_messages(
        context.messages,
        model,
        lambda tool_call_id, _m, _src: normalize_tool_call_id(tool_call_id),
    )
    deferred = deferred_tool_names or set()
    loaded_tool_names: Set[str] = set()

    params: List[Dict[str, Any]] = []
    i = 0
    while i < len(transformed):
        message = transformed[i]

        if message.role == "user":
            if isinstance(message.content, str):
                if message.content.strip():
                    params.append(
                        {
                            "role": "user",
                            "content": sanitize_surrogates(message.content),
                        }
                    )
            else:
                blocks: List[Dict[str, Any]] = []
                for item in message.content:
                    if isinstance(item, TextContent):
                        if item.text.strip():
                            blocks.append(
                                {
                                    "type": "text",
                                    "text": sanitize_surrogates(item.text),
                                }
                            )
                    elif isinstance(item, ImageContent):
                        blocks.append(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": item.mime_type,
                                    "data": item.data,
                                },
                            }
                        )
                if blocks:
                    params.append({"role": "user", "content": blocks})

        elif message.role == "assistant":
            blocks = []
            for block in message.content:
                if block.type == "text":
                    if not block.text.strip():
                        continue
                    blocks.append(
                        {"type": "text", "text": sanitize_surrogates(block.text)}
                    )
                elif block.type == "thinking":
                    # redacted：不透明载荷原样回传（对齐 TS）
                    if block.redacted:
                        blocks.append(
                            {
                                "type": "redacted_thinking",
                                "data": block.thinking_signature,
                            }
                        )
                        continue
                    signature = block.thinking_signature
                    has_signature = bool(signature and signature.strip())
                    if not block.thinking.strip() and not has_signature:
                        continue
                    if not has_signature:
                        # 缺签名（如流被 abort）：降级纯文本；标记模型可保留空签名块
                        if compat.allow_empty_signature:
                            blocks.append(
                                {
                                    "type": "thinking",
                                    "thinking": sanitize_surrogates(block.thinking),
                                    "signature": "",
                                }
                            )
                        else:
                            blocks.append(
                                {
                                    "type": "text",
                                    "text": sanitize_surrogates(block.thinking),
                                }
                            )
                    else:
                        blocks.append(
                            {
                                "type": "thinking",
                                "thinking": sanitize_surrogates(block.thinking),
                                "signature": signature,
                            }
                        )
                elif block.type == "toolCall":
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.arguments or {},
                        }
                    )
            if blocks:
                params.append({"role": "assistant", "content": blocks})

        elif isinstance(message, ToolResultMessage):
            # 连续的 toolResult 合并进一条 user 消息（z.ai 同款端点要求，
            # 对齐 TS 的分组循环）；tool_reference 兄弟块排在全部结果块之后
            tool_results: List[Dict[str, Any]] = []
            siblings: List[Dict[str, Any]] = []
            j = i
            while j < len(transformed):
                current = transformed[j]
                if not isinstance(current, ToolResultMessage):
                    break
                tool_result, sibling = _convert_tool_result(
                    current, deferred, loaded_tool_names
                )
                tool_results.append(tool_result)
                siblings.extend(sibling)
                j += 1
            params.append({"role": "user", "content": [*tool_results, *siblings]})
            i = j - 1  # 跳过已处理的连续段

        i += 1

    return _apply_cache_control(params, cache_control)


def convert_tools(
    tools: List[Tool],
    compat: ResolvedAnthropicCompat,
    cache_control: Optional[CacheControl] = None,
    defer_loading: bool = False,
) -> List[Dict[str, Any]]:
    """转换工具定义为 Anthropic 形状（对齐 TS convertTools）。

    - 非 strict（默认）：``input_schema`` 只保留 type/properties/required
      三键（对齐 TS legacyInputSchema——其余键丢弃）；
    - strict（``supports_strict_tools``）：完整 parameters + strict 标记；
    - ``supports_eager_tool_input_streaming`` 时逐工具带
      ``eager_input_streaming``（不支持的端点由客户端 beta 头兜底，见
      client.py）；``defer_loading`` 标记延迟装载工具；
    - cache_control 打在最后一条工具上（对齐 TS index === length-1）。
    """
    result: List[Dict[str, Any]] = []
    for index, tool in enumerate(tools):
        parameters = tool.parameters or {}
        legacy_input_schema: Dict[str, Any] = {
            "type": "object",
            "properties": parameters.get("properties") or {},
            "required": parameters.get("required") or [],
        }
        input_schema = (
            {**parameters, **legacy_input_schema}
            if compat.supports_strict_tools
            else legacy_input_schema
        )
        entry: Dict[str, Any] = {
            "name": tool.name,
            "description": tool.description,
            "input_schema": input_schema,
        }
        if compat.supports_eager_tool_input_streaming:
            entry["eager_input_streaming"] = True
        if compat.supports_strict_tools:
            entry["strict"] = True
        if defer_loading:
            entry["defer_loading"] = True
        if cache_control and index == len(tools) - 1:
            entry["cache_control"] = cache_control
        result.append(entry)
    return result


def _apply_cache_control(
    params: List[Dict[str, Any]], cache_control: Optional[CacheControl]
) -> List[Dict[str, Any]]:
    """给最后一条 user 消息的最后一个块打 cache_control（对齐 TS 收尾段）。"""
    if not cache_control or not params:
        return params
    last_message = params[-1]
    if last_message.get("role") != "user":
        return params
    content = last_message.get("content")
    if isinstance(content, str):
        last_message["content"] = [
            {"type": "text", "text": content, "cache_control": cache_control}
        ]
    elif isinstance(content, list) and content:
        last_block = content[-1]
        if isinstance(last_block, dict) and last_block.get("type") in (
            "text",
            "image",
            "tool_result",
        ):
            last_block["cache_control"] = cache_control
    return params
