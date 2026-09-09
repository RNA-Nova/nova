"""消息与工具转换（对齐 TS convertMessages / convertTools / hasToolHistory 等）。"""

import json
import re
from typing import Any, Dict, List, Optional, Set

from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionToolParam,
)

from ...types.compat import OpenAICompletionsCompat
from ...types.messages import Context, Message, Tool
from ...types.model import Model
from ...utils.hash import short_hash
from ...utils.surrogate import sanitize_surrogates
from .._shared.transform_messages import transform_messages
from .reasoning import (
    is_reasoning_field,
    parse_legacy_encrypted_reasoning_detail,
    parse_openai_reasoning_details,
)


def has_tool_history(messages: List[Message]) -> bool:
    """检查对话消息是否包含工具调用或工具结果（对齐 TS hasToolHistory）。"""
    for msg in messages:
        if msg.role == "toolResult":
            return True
        if msg.role == "assistant":
            if any(block.type == "toolCall" for block in msg.content):
                return True
    return False


def get_deferred_tool_names(messages: List[Message]) -> Set[str]:
    """收集 toolResult 消息中新增注册的工具名（对齐 TS getDeferredToolNames）。"""
    names: Set[str] = set()
    for message in messages:
        if message.role == "toolResult":
            for name in getattr(message, "added_tool_names", None) or []:
                names.add(name)
    return names


def get_tools_by_name(tools: Optional[List[Tool]], names: Set[str]) -> List[Tool]:
    """按名称查找工具定义（对齐 TS getToolsByName）。"""
    if not tools:
        return []
    tools_by_name = {tool.name: tool for tool in tools}
    return [tools_by_name[name] for name in names if name in tools_by_name]


def convert_messages(
    model: Model, context: Context, compat: OpenAICompletionsCompat
) -> List[ChatCompletionMessageParam]:
    """把内部消息列表转换为 OpenAI Chat Completions 参数（对齐 TS convertMessages）。"""

    def normalize_tool_call_id(id: str) -> str:
        """规范化工具调用 ID（对齐 TS normalizeToolCallId 终态）。

        处理 OpenAI Responses API 的 ``{call_id}|{item_id}`` 管道形态：
        同一回合的多个 tool call 共享 call_id、item_id 不同——回放到
        Chat Completions 必须保留 item 级唯一性，否则 id 冲突、结果配错。
        超长时用 shortHash 截断（OpenAI 上限 40 字符）。
        """
        if "|" in id:
            separator_index = id.index("|")
            call_id = re.sub(r"[^a-zA-Z0-9_-]", "_", id[:separator_index])
            item_id = re.sub(r"[^a-zA-Z0-9_-]", "_", id[separator_index + 1 :])
            combined_id = f"{call_id}_{item_id}" if item_id else call_id
            if len(combined_id) <= 40:
                return combined_id
            digest = short_hash(id)[:8]
            prefix = call_id[: max(1, 40 - len(digest) - 1)]
            return f"{prefix}_{digest}"
        if model.provider == "openai":
            return id[:40] if len(id) > 40 else id
        return id

    transformed_messages = transform_messages(
        context.messages, model, lambda id, m, src: normalize_tool_call_id(id)
    )

    params: List[ChatCompletionMessageParam] = []

    if context.system_prompt:
        use_developer_role = model.reasoning and compat.supports_developer_role
        role = "developer" if use_developer_role else "system"
        params.append(
            {"role": role, "content": sanitize_surrogates(context.system_prompt)}
        )

    last_role: Optional[str] = None
    i = 0
    while i < len(transformed_messages):
        msg = transformed_messages[i]

        if (
            compat.requires_assistant_after_tool_result
            and last_role == "toolResult"
            and msg.role == "user"
        ):
            params.append(
                {"role": "assistant", "content": "I have processed the tool results."}
            )

        if msg.role == "user":
            user_msg = msg
            if isinstance(user_msg.content, str):
                params.append(
                    {"role": "user", "content": sanitize_surrogates(user_msg.content)}
                )
            else:
                content = []
                for item in user_msg.content:
                    if item.type == "text":
                        content.append(
                            {"type": "text", "text": sanitize_surrogates(item.text)}
                        )
                    elif item.type == "image":
                        content.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{item.mime_type};base64,{item.data}"
                                },
                            }
                        )
                if not content:
                    i += 1
                    continue
                params.append({"role": "user", "content": content})

        elif msg.role == "assistant":
            assistant_msg = msg
            assistant_param: ChatCompletionAssistantMessageParam = {
                "role": "assistant",
                "content": "" if compat.requires_assistant_after_tool_result else None,
            }

            text_blocks = [b for b in assistant_msg.content if b.type == "text"]
            non_empty_text = [b for b in text_blocks if b.text and b.text.strip()]
            assistant_text_parts = [
                {"type": "text", "text": sanitize_surrogates(b.text)}
                for b in non_empty_text
            ]
            assistant_text = "".join(p["text"] for p in assistant_text_parts)

            thinking_blocks = [b for b in assistant_msg.content if b.type == "thinking"]
            tool_calls = [b for b in assistant_msg.content if b.type == "toolCall"]

            # reasoning_details 双路（对齐 TS signedReasoningDetails ?? legacy）：
            # 主路从 thinking 块签名的 JSON 数组解析（流式归档产物）；
            # 兜底路从旧会话 toolCall.thought_signature 里的单个加密 detail 解析。
            signed_reasoning_details = next(
                (
                    details
                    for details in (
                        parse_openai_reasoning_details(b.thinking_signature)
                        for b in thinking_blocks
                    )
                    if details is not None
                ),
                None,
            )
            legacy_reasoning_details = [
                detail
                for detail in (
                    parse_legacy_encrypted_reasoning_detail(tc.thought_signature)
                    for tc in tool_calls
                )
                if detail is not None
            ]
            preserved_reasoning_details = (
                signed_reasoning_details
                if signed_reasoning_details is not None
                else (legacy_reasoning_details or None)
            )

            non_empty_thinking = [
                b for b in thinking_blocks if b.thinking and b.thinking.strip()
            ]

            if non_empty_thinking:
                if compat.requires_thinking_as_text:
                    thinking_text = "\n\n".join(
                        sanitize_surrogates(b.thinking) for b in non_empty_thinking
                    )
                    assistant_param["content"] = [
                        {"type": "text", "text": thinking_text},
                        *assistant_text_parts,
                    ]
                else:
                    if assistant_text:
                        assistant_param["content"] = assistant_text
                    if preserved_reasoning_details is None:
                        # 无结构化 details 时按原始 reasoning 字段回放
                        # （llama.cpp server + gpt-oss 场景）。
                        # 白名单护栏：thinking_signature 只可能是三个合法字段名
                        # 之一——绝不能把任意字符串当请求体字段名发送。
                        signature = non_empty_thinking[0].thinking_signature
                        if model.provider == "opencode-go" and signature == "reasoning":
                            signature = "reasoning_content"
                        if signature and is_reasoning_field(signature):
                            assistant_param[signature] = "\n".join(
                                b.thinking for b in non_empty_thinking
                            )
            elif assistant_text:
                assistant_param["content"] = assistant_text

            if tool_calls:
                assistant_param["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in tool_calls
                ]
            if preserved_reasoning_details is not None:
                assistant_param["reasoning_details"] = preserved_reasoning_details

            if (
                compat.requires_reasoning_content_on_assistant_messages
                and model.reasoning
                and assistant_param.get("reasoning_content") is None
            ):
                assistant_param["reasoning_content"] = ""

            content = assistant_param.get("content")
            has_content = content is not None and (
                (isinstance(content, str) and len(content) > 0)
                or (isinstance(content, list) and len(content) > 0)
            )
            if not has_content and "tool_calls" not in assistant_param:
                i += 1
                continue
            params.append(assistant_param)

        elif msg.role == "toolResult":
            image_blocks = []
            deferred_tool_names: Set[str] = set()

            j = i
            while (
                j < len(transformed_messages)
                and transformed_messages[j].role == "toolResult"
            ):
                curr = transformed_messages[j]

                text_result = "\n".join(
                    c.text for c in curr.content if c.type == "text"
                )
                has_images = any(c.type == "image" for c in curr.content)

                has_text = len(text_result) > 0
                if has_text:
                    tool_result_text = text_result
                elif has_images:
                    tool_result_text = "(see attached image)"
                else:
                    tool_result_text = "(no tool output)"
                tool_result_param: ChatCompletionToolMessageParam = {
                    "role": "tool",
                    "content": sanitize_surrogates(tool_result_text),
                    "tool_call_id": curr.tool_call_id,
                }
                if compat.requires_tool_result_name and curr.tool_name:
                    tool_result_param["name"] = curr.tool_name  # type: ignore[typeddict-unknown-key]
                params.append(tool_result_param)

                if compat.deferred_tools_mode == "kimi":
                    for name in getattr(curr, "added_tool_names", None) or []:
                        deferred_tool_names.add(name)

                if has_images and "image" in model.input_types:
                    for block in curr.content:
                        if block.type == "image":
                            image_blocks.append(
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{block.mime_type};base64,{block.data}"
                                    },
                                }
                            )
                j += 1

            i = j - 1

            if image_blocks:
                if compat.requires_assistant_after_tool_result:
                    params.append(
                        {
                            "role": "assistant",
                            "content": "I have processed the tool results.",
                        }
                    )
                params.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Attached image(s) from tool result:",
                            },
                            *image_blocks,
                        ],
                    }
                )
                last_role = "user"
            else:
                last_role = "toolResult"

            if compat.deferred_tools_mode == "kimi" and deferred_tool_names:
                deferred_tools = get_tools_by_name(context.tools, deferred_tool_names)
                if deferred_tools:
                    params.append(
                        {
                            "role": "system",
                            "tools": convert_tools(deferred_tools, compat),
                        }
                    )

            i += 1
            continue

        last_role = msg.role
        i += 1

    return params


def convert_tools(
    tools: List[Tool], compat: OpenAICompletionsCompat
) -> List[ChatCompletionToolParam]:
    """转换工具定义为 OpenAI 格式（对齐 TS convertTools）。"""
    result = []
    for tool in tools:
        tool_dict = {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        if compat.supports_strict_mode is not False:
            tool_dict["function"]["strict"] = False
        result.append(tool_dict)
    return result
