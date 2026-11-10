"""消息与工具转换（对齐 TS ``convertMessages`` / ``convertTools`` /
``normalizeToolCallId``）。

Anthropic 消息形态要点：

- system 是顶层参数不是消息（归 params.py）；中途 system 消息在穿过模式
  下渲染为 ``role: system`` 内容块序列——文本块 + INLINE_TOOLS 的
  ``tool_addition``/``tool_removal``（工具变更随消息按值下发，顶层工具
  列表固定不动，缓存前缀存活——pi b271b0a52 起的形态）；
- assistant 内容块保真回放：text / thinking（**签名原样回传**，缺签名降级
  纯文本——``allow_empty_signature`` 的模型除外）/ redacted_thinking /
  tool_use；
- toolResult 消息合并为 user 消息的 tool_result 块；
- cache_control 打在**最后一条 user/system 消息的最后一个块**上（缓存
  会话历史）；
- strict 工具的按工具裁决与 schema 净化归 ``constrained.py``（pi
  constrained-sampling.ts 对位）。
"""

import re
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple, TypeGuard

from nova_protocol import (
    Context,
    ImageContent,
    Model,
    TextContent,
    Tool,
    ToolResultMessage,
)

from ...utils.surrogate import sanitize_surrogates
from .._shared.prompt_cache import CacheControl
from .._shared.transcript import render_system_message_update
from .._shared.transform_messages import transform_messages
from .compat import ResolvedAnthropicCompat
from .constrained import (
    make_strict_json_schema,
    prefers_json_schema_strict_sampling,
    resolve_json_schema_strict_sampling,
)
from .tool_names import to_claude_code_name

# Anthropic 工具调用 id 的合法形状（对齐 TS normalizeToolCallId）
_TOOL_CALL_ID_PATTERN = re.compile(r"[^a-zA-Z0-9_-]")

# pi ``AnthropicEffort`` 合法档（回放过滤——非法值不上线，防跨源转录 400）
_ANTHROPIC_EFFORT_LEVELS = frozenset(["low", "medium", "high", "xhigh", "max"])


def is_anthropic_effort(value: Any) -> TypeGuard[str]:
    """合法 effort 档判定（对齐 TS ``isAnthropicEffort``）。"""
    return value in _ANTHROPIC_EFFORT_LEVELS


class ConvertedAnthropicMessages(NamedTuple):
    """convert_messages 的产物（对齐 TS ``ConvertedAnthropicMessages``）。"""

    messages: List[Dict[str, Any]]
    # wire 序号 → 该条 assistant 回复的端点原生 effort 档（仅 managed
    # effort 形态且 api/provider/合法性三重过滤通过时记录——pi 同款，
    # **无 stop_reason 过滤**：TOOL_USE/LENGTH 回复同样记档）
    assistant_levels: Dict[int, str]


def normalize_tool_call_id(tool_call_id: str) -> str:
    """规范化工具调用 id（非法字符换下划线，截断到 64——对齐 TS）。"""
    return _TOOL_CALL_ID_PATTERN.sub("_", tool_call_id)[:64]


def _api_value(message: Any) -> str:
    """消息的 api 字段归一为字符串（KnownApi 枚举/裸字符串双形态）。"""
    api = getattr(message, "api", None)
    return getattr(api, "value", api) or ""


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
    content_override: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """toolResult → tool_result 块（对齐 TS convertToolResult）。"""
    return {
        "type": "tool_result",
        "tool_use_id": message.tool_call_id,
        "content": _convert_content_blocks(
            message.content if content_override is None else content_override
        ),
        "is_error": message.is_error,
    }


# 错误工具结果的图片占位文案（对齐 pi ERROR_TOOL_IMAGE_PLACEHOLDER——
# Anthropic 拒收错误结果里的非文本内容）
_ERROR_TOOL_IMAGE_PLACEHOLDER = "(image attached after the tool results)"


def _replace_images_with_placeholder(content: List[Any], placeholder: str) -> List[Any]:
    """图片连段塌缩为单个占位文本（对齐 pi replaceImagesWithPlaceholder）。"""
    result: List[Any] = []
    previous_was_placeholder = False
    for block in content:
        if block.type == "image":
            if not previous_was_placeholder:
                result.append(TextContent(type="text", text=placeholder))
            previous_was_placeholder = True
            continue
        result.append(block)
        previous_was_placeholder = getattr(block, "text", None) == placeholder
    return result


def _split_error_tool_result_images(
    message: ToolResultMessage,
) -> Optional[Tuple[List[Any], str, List[Any]]]:
    """错误工具结果拆图（对齐 pi splitErrorToolResultImages）：错误结果带图
    时拆成（图换占位的文本内容, 命名工具调用的 caption, 图片块）——图片
    随本轮全部 tool_result 之后在同一 user 消息送达（#10794）。无需拆分
    （非错误/无图）返回 ``None``。"""
    if not message.is_error:
        return None
    images = [block for block in message.content if block.type == "image"]
    if not images:
        return None
    return (
        _replace_images_with_placeholder(
            message.content, _ERROR_TOOL_IMAGE_PLACEHOLDER
        ),
        f"Images from the error result of tool call {message.tool_call_id} ({message.tool_name}):",
        images,
    )


def convert_messages(
    model: Model,
    context: Context,
    compat: ResolvedAnthropicCompat,
    cache_control: Optional[CacheControl],
    convert_tool_definitions: Optional[
        Callable[[List[Tool]], List[Dict[str, Any]]]
    ] = None,
    managed_provider: Optional[str] = None,
    is_oauth: bool = False,
) -> "ConvertedAnthropicMessages":
    """把内部消息列表转换为 Anthropic MessageParam 形状（对齐 TS convertMessages）。

    ``convert_tool_definitions``:INLINE_TOOLS 形态下把 SystemMessage 的
    ``tools_added`` 转成 ``tool_addition`` 定义块的转换器（对齐 TS 同名
    闭包参数）；``None`` = 非 INLINE 形态，中途 system 消息只发文本块。

    ``managed_provider``:mid-convo effort 形态时传 ``model.provider``
    （对齐 TS 同名参数）——开启 wire 索引 effort 档位记录
    （``assistant_levels``），否则不记。
    """
    transformed = transform_messages(
        context.messages,
        model,
        lambda tool_call_id, _m, _src: normalize_tool_call_id(tool_call_id),
    )

    params: List[Dict[str, Any]] = []
    # wire 序号 → effort 档（对齐 TS assistantLevels）
    assistant_levels: Dict[int, str] = {}
    # 中途 system 消息的 held-back 队列（对齐 TS convertMessages）：渲染后
    # 先入队，在下一个**非空** assistant 入参前 flush——被丢弃的空 assistant
    # 不触发（防止更新落在上一条 user 消息之前）；末尾清空余量。
    pending_system: List[Dict[str, Any]] = []

    def _flush_pending_system() -> None:
        params.extend(pending_system)
        pending_system.clear()

    i = 0
    while i < len(transformed):
        message = transformed[i]

        if message.role == "system":
            # 模型接受中途 system 消息时才会走到这里（否则转录已折叠）。
            # INLINE_TOOLS 形态：工具变更按值随消息下发（对齐 TS——移除名
            # 被同名新增覆盖时跳过 removal；addition 带完整定义，同名重定义
            # 天然可表达）
            blocks: List[Dict[str, Any]] = []
            text = sanitize_surrogates(render_system_message_update(message))
            if text.strip():
                blocks.append({"type": "text", "text": text})
            if convert_tool_definitions is not None:
                added = message.tools_added or []
                redefined = {tool.name for tool in added}
                for ref in message.tools_removed or []:
                    if ref.name in redefined:
                        continue
                    blocks.append(
                        {
                            "type": "tool_removal",
                            "tool": {
                                "type": "tool_reference",
                                "name": (
                                    to_claude_code_name(ref.name)
                                    if is_oauth
                                    else ref.name
                                ),
                            },
                        }
                    )
                for definition in convert_tool_definitions(added):
                    blocks.append(
                        {
                            "type": "tool_addition",
                            "tool": {
                                "type": "tool_definition",
                                "definition": definition,
                            },
                        }
                    )
            if blocks:
                pending_system.append({"role": "system", "content": blocks})

        elif message.role == "user":
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
                            "name": (
                                to_claude_code_name(block.name)
                                if is_oauth
                                else block.name
                            ),
                            "input": block.arguments or {},
                        }
                    )
            if blocks:
                _flush_pending_system()  # held-back：assistant 入参前发出
                wire_index = len(params)
                params.append({"role": "assistant", "content": blocks})
                # effort 档位记录（对齐 TS assistantLevels）：managed 形态 +
                # api/provider/合法档三重过滤——无 stop_reason 过滤，
                # TOOL_USE/LENGTH 回复同样记档
                if (
                    managed_provider is not None
                    and _api_value(message) == "anthropic-messages"
                    and message.provider == managed_provider
                    and is_anthropic_effort(message.provider_thinking_level)
                ):
                    assistant_levels[wire_index] = message.provider_thinking_level

        elif isinstance(message, ToolResultMessage):
            # 连续的 toolResult 合并进一条 user 消息（z.ai 同款端点要求，
            # 对齐 TS 的分组循环）；错误结果的图片拆出随全部结果之后送达
            # （Anthropic 拒收错误结果里的非文本内容——#10794，对齐 TS）
            tool_results: List[Dict[str, Any]] = []
            error_images: List[Dict[str, Any]] = []
            j = i
            while j < len(transformed):
                current = transformed[j]
                if not isinstance(current, ToolResultMessage):
                    break
                split = _split_error_tool_result_images(current)
                tool_results.append(
                    _convert_tool_result(
                        current, content_override=split[0] if split else None
                    )
                )
                if split is not None:
                    _, caption, images = split
                    error_images.append({"type": "text", "text": caption})
                    for image in images:
                        error_images.append(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": image.mime_type,
                                    "data": image.data,
                                },
                            }
                        )
                j += 1
            params.append({"role": "user", "content": [*tool_results, *error_images]})
            i = j - 1  # 跳过已处理的连续段

        i += 1

    _flush_pending_system()  # held-back 余量收尾（对齐 TS 循环后 flush）
    return ConvertedAnthropicMessages(
        messages=_apply_cache_control(params, cache_control),
        assistant_levels=assistant_levels,
    )


def convert_tools(
    tools: List[Tool],
    compat: ResolvedAnthropicCompat,
    cache_control: Optional[CacheControl] = None,
    disable_preferred_strict_tools: bool = False,
    is_oauth: bool = False,
) -> List[Dict[str, Any]]:
    """转换工具定义为 Anthropic 形状（对齐 TS convertTools）。

    - 非 strict（默认）：``input_schema`` 保留 type/properties/required
      三键 + ``$defs``/``definitions``（属性 $refs 要指向共享定义——
      只留三键会让 $ref 悬空，Claude 产出错误参数；对齐 TS 4a6a58eb5），
      其余根键（anyOf/oneOf/allOf）不进（Anthropic 拒收 400）；
    - strict：**按工具裁决**（对位 pi——工具声明
      ``constrained_sampling`` 且模型 ``supports_strict_tools`` 且
      schema 过净化才启用；strict 的 ``input_schema`` 是净化后形态：
      required=全部属性、additionalProperties=false、可空可选包 anyOf），
      带 strict 标记。``disable_preferred_strict_tools`` 为真时 prefer
      工具一律降级非严格（strict 限量 400 后的重发路径——pi 同款）；
    - ``supports_eager_tool_input_streaming`` 时逐工具带
      ``eager_input_streaming``（不支持的端点由客户端 beta 头兜底，见
      client.py）；
    - cache_control 打在最后一条工具上（对齐 TS index === length-1）。
    """
    result: List[Dict[str, Any]] = []
    for index, tool in enumerate(tools):
        parameters = tool.parameters or {}
        strict = (
            None
            if disable_preferred_strict_tools
            and prefers_json_schema_strict_sampling(tool)
            else resolve_json_schema_strict_sampling(tool, compat.supports_strict_tools)
        )
        if strict:
            input_schema = make_strict_json_schema(parameters)
        else:
            legacy_input_schema: Dict[str, Any] = {
                "type": "object",
                "properties": parameters.get("properties") or {},
                "required": parameters.get("required") or [],
            }
            # 保共享定义（$ref 可解析）；其余根键不进（Anthropic 拒收）
            if parameters.get("$defs") is not None:
                legacy_input_schema["$defs"] = parameters["$defs"]
            if parameters.get("definitions") is not None:
                legacy_input_schema["definitions"] = parameters["definitions"]
            input_schema = legacy_input_schema
        entry: Dict[str, Any] = {
            "name": to_claude_code_name(tool.name) if is_oauth else tool.name,
            "description": tool.description,
            "input_schema": input_schema,
        }
        if compat.supports_eager_tool_input_streaming:
            entry["eager_input_streaming"] = True
        if strict:
            entry["strict"] = True
        if cache_control and index == len(tools) - 1:
            entry["cache_control"] = cache_control
        result.append(entry)
    return result


def _apply_cache_control(
    params: List[Dict[str, Any]], cache_control: Optional[CacheControl]
) -> List[Dict[str, Any]]:
    """给最后一条 user/system 消息的最后一个块打 cache_control（对齐 TS
    收尾段——held-back flush 后末条可能是 system 消息）。"""
    if not cache_control or not params:
        return params
    last_message = params[-1]
    if last_message.get("role") not in ("user", "system"):
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
            "tool_addition",
            "tool_removal",
        ):
            last_block["cache_control"] = cache_control
    return params
