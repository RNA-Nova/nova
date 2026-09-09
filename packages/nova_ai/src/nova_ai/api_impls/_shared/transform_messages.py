"""
消息转换工具
用于跨提供商兼容性的消息转换
"""

import time
from copy import deepcopy
from typing import Callable, Dict, List, Optional, Set

from ...types.content import ContentUnion, TextContent, ToolCall
from ...types.enums import StopReason
from ...types.messages import AssistantMessage, Message, ToolResultMessage
from ...types.model import Model

NON_VISION_USER_IMAGE_PLACEHOLDER = "(image omitted: model does not support images)"
NON_VISION_TOOL_IMAGE_PLACEHOLDER = (
    "(tool image omitted: model does not support images)"
)


def _replace_images_with_placeholder(
    content: List[ContentUnion], placeholder: str
) -> List[TextContent]:
    """把 content 中的 image 替换为 placeholder 文本（对齐 TS）。"""
    result: List[TextContent] = []
    previous_was_placeholder = False
    for block in content:
        if block.type == "image":
            if not previous_was_placeholder:
                result.append(TextContent(type="text", text=placeholder))
            previous_was_placeholder = True
            continue
        result.append(block)
        previous_was_placeholder = (
            isinstance(block, TextContent) and block.text == placeholder
        )
    return result


def _downgrade_unsupported_images(
    messages: List[Message], model: Model
) -> List[Message]:
    """模型不支持 image 时，把 image 内容降级为 placeholder（对齐 TS）。"""
    if "image" in model.input_types:
        return messages

    downgraded: List[Message] = []
    for msg in messages:
        if msg.role == "user" and isinstance(msg.content, list):
            downgraded.append(
                msg.model_copy(
                    update={
                        "content": _replace_images_with_placeholder(
                            msg.content, NON_VISION_USER_IMAGE_PLACEHOLDER
                        )
                    }
                )
            )
            continue
        if msg.role == "toolResult":
            downgraded.append(
                msg.model_copy(
                    update={
                        "content": _replace_images_with_placeholder(
                            msg.content, NON_VISION_TOOL_IMAGE_PLACEHOLDER
                        )
                    }
                )
            )
            continue
        downgraded.append(msg)
    return downgraded


def transform_messages(
    messages: List[Message],
    model: Model,
    normalize_tool_call_id: Optional[
        Callable[[str, Model, AssistantMessage], str]
    ] = None,
) -> List[Message]:
    """
    转换消息以实现跨提供商兼容性

    Args:
        messages: 原始消息列表
        model: 目标模型
        normalize_tool_call_id: 可选的工具调用ID规范化函数

    Returns:
        转换后的消息列表
    """
    # 构建原始工具调用ID到规范化ID的映射
    tool_call_id_map: Dict[str, str] = {}

    # 对齐 TS：先处理 null content 和 image 降级
    normalized_messages = [
        msg if msg.content is not None else msg.model_copy(update={"content": []})
        for msg in messages
    ]
    image_aware_messages = _downgrade_unsupported_images(normalized_messages, model)

    # 第一遍：转换消息（思考块、工具调用ID规范化）
    transformed: List[Message] = []

    for msg in image_aware_messages:
        # 用户消息保持不变
        if msg.role == "user":
            transformed.append(msg)
            continue

        # 处理 toolResult 消息 - 如果有映射则规范化 toolCallId
        if msg.role == "toolResult":
            tool_result = msg
            normalized_id = tool_call_id_map.get(tool_result.tool_call_id)
            if normalized_id and normalized_id != tool_result.tool_call_id:
                # 创建新的 toolResult 消息，更新 toolCallId；
                # model_copy 保留 details / added_tool_names 等其余字段
                transformed.append(
                    tool_result.model_copy(update={"tool_call_id": normalized_id})
                )
            else:
                transformed.append(msg)
            continue

        # 助手消息需要转换检查
        if msg.role == "assistant":
            assistant_msg = msg
            is_same_model = (
                assistant_msg.provider == model.provider
                and assistant_msg.api == model.api
                and assistant_msg.model == model.id
            )

            transformed_content: List[ContentUnion] = []

            for block in assistant_msg.content:
                if block.type == "thinking":
                    thinking_block = block

                    # 被屏蔽的思考是加密的不透明内容，仅对同一模型有效
                    # 跨模型时删除以避免API错误
                    if thinking_block.redacted:
                        if is_same_model:
                            transformed_content.append(thinking_block)
                        continue

                    # 对于同一模型：保留带有签名的思考块（用于重放）
                    # 即使思考文本为空（OpenAI加密推理）
                    if is_same_model and thinking_block.thinking_signature:
                        transformed_content.append(thinking_block)
                        continue

                    # 跳过空的思考块，其他转换为纯文本
                    if (
                        not thinking_block.thinking
                        or thinking_block.thinking.strip() == ""
                    ):
                        continue

                    if is_same_model:
                        transformed_content.append(thinking_block)
                    else:
                        # 转换为文本块
                        transformed_content.append(
                            TextContent(type="text", text=thinking_block.thinking)
                        )

                elif block.type == "text":
                    text_block = block
                    if is_same_model:
                        transformed_content.append(text_block)
                    else:
                        # 保留文本内容
                        transformed_content.append(
                            TextContent(
                                type="text",
                                text=text_block.text,
                                text_signature=(
                                    text_block.text_signature if is_same_model else None
                                ),
                            )
                        )

                elif block.type == "toolCall":
                    tool_call = block
                    normalized_tool_call = deepcopy(tool_call)

                    # 跨模型时删除 thought_signature
                    if not is_same_model and tool_call.thought_signature:
                        normalized_tool_call.thought_signature = None

                    # 规范化工具调用ID
                    if not is_same_model and normalize_tool_call_id:
                        normalized_id = normalize_tool_call_id(
                            tool_call.id, model, assistant_msg
                        )
                        if normalized_id != tool_call.id:
                            tool_call_id_map[tool_call.id] = normalized_id
                            normalized_tool_call.id = normalized_id

                    transformed_content.append(normalized_tool_call)

                else:
                    # 其他类型保持不变
                    transformed_content.append(block)

            # 创建新的助手消息；model_copy 保留 diagnostics / response_id /
            # response_model 等未显式修改的字段（对齐 TS 的对象展开）
            new_assistant_msg = assistant_msg.model_copy(
                update={"content": transformed_content}
            )
            transformed.append(new_assistant_msg)
            continue

        # 其他类型保持不变
        transformed.append(msg)

    # 第二遍：为孤立的工具调用插入合成的空工具结果
    # 这可以保留思考签名并满足API要求
    result: List[Message] = []
    pending_tool_calls: List[ToolCall] = []
    existing_tool_result_ids: Set[str] = set()

    for i, msg in enumerate(transformed):

        if msg.role == "assistant":
            assistant_msg = msg

            # 如果有待处理的孤立工具调用，现在插入合成结果
            if pending_tool_calls:
                for tc in pending_tool_calls:
                    if tc.id not in existing_tool_result_ids:
                        result.append(
                            ToolResultMessage(
                                role="toolResult",
                                tool_call_id=tc.id,
                                tool_name=tc.name,
                                content=[
                                    TextContent(type="text", text="No result provided")
                                ],
                                is_error=True,
                                timestamp=int(time.time() * 1000),  # 当前时间戳（毫秒）
                            )
                        )
                pending_tool_calls = []
                existing_tool_result_ids = set()

            # 跳过错误/中止的助手消息
            # 这些是不完整的历史记录，不应该重放：
            # - 可能有部分内容（推理而没有消息，不完整的工具调用）
            # - 重放它们可能导致API错误（例如OpenAI "reasoning without following item"）
            # - 模型应该从最后一个有效状态重试
            if assistant_msg.stop_reason in [StopReason.ERROR, StopReason.ABORTED]:
                continue

            # 跟踪此助手消息中的工具调用
            tool_calls = [
                block for block in assistant_msg.content if block.type == "toolCall"
            ]
            if tool_calls:
                pending_tool_calls = tool_calls
                existing_tool_result_ids = set()

            result.append(msg)

        elif msg.role == "toolResult":
            tool_result = msg
            existing_tool_result_ids.add(tool_result.tool_call_id)
            result.append(msg)

        elif msg.role == "user":
            # 用户消息中断工具流 - 为孤立调用插入合成结果
            if pending_tool_calls:
                for tc in pending_tool_calls:
                    if tc.id not in existing_tool_result_ids:
                        result.append(
                            ToolResultMessage(
                                role="toolResult",
                                tool_call_id=tc.id,
                                tool_name=tc.name,
                                content=[
                                    TextContent(type="text", text="No result provided")
                                ],
                                is_error=True,
                                timestamp=int(time.time() * 1000),
                            )
                        )
                pending_tool_calls = []
                existing_tool_result_ids = set()
            result.append(msg)

        else:
            result.append(msg)

    # 对话结束时仍有未解析的工具调用：现在合成结果（对齐 TS）
    if pending_tool_calls:
        for tc in pending_tool_calls:
            if tc.id not in existing_tool_result_ids:
                result.append(
                    ToolResultMessage(
                        role="toolResult",
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                        content=[TextContent(type="text", text="No result provided")],
                        is_error=True,
                        timestamp=int(time.time() * 1000),
                    )
                )

    return result
