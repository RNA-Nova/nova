"""
消息转换工具
用于跨提供商兼容性的消息转换
"""

from typing import List, Dict, Optional, Set, Callable
import time
from copy import deepcopy

from ..types.messages import (
    Message, AssistantMessage, ToolResultMessage
)
from ..types.content import (
    TextContent, ToolCall, ContentUnion
)
from ..types.model import Model
from ..types.enums import StopReason


def transform_messages(
    messages: List[Message],
    model: Model,
    normalize_tool_call_id: Optional[Callable[[str, Model, AssistantMessage], str]] = None
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
    
    # 第一遍：转换消息（思考块、工具调用ID规范化）
    transformed: List[Message] = []
    
    for msg in messages:
        # 用户消息保持不变
        if msg.role == "user":
            transformed.append(msg)
            continue
        
        # 处理 toolResult 消息 - 如果有映射则规范化 toolCallId
        if msg.role == "toolResult":
            tool_result = msg
            normalized_id = tool_call_id_map.get(tool_result.tool_call_id)
            if normalized_id and normalized_id != tool_result.tool_call_id:
                # 创建新的 toolResult 消息，更新 toolCallId
                new_tool_result = ToolResultMessage(
                    role="toolResult",
                    tool_call_id=normalized_id,
                    tool_name=tool_result.tool_name,
                    content=tool_result.content,
                    details=tool_result.details,
                    is_error=tool_result.is_error,
                    timestamp=tool_result.timestamp
                )
                transformed.append(new_tool_result)
            else:
                transformed.append(msg)
            continue
        
        # 助手消息需要转换检查
        if msg.role == "assistant":
            assistant_msg = msg
            is_same_model = (
                assistant_msg.provider == model.provider and
                assistant_msg.api == model.api and
                assistant_msg.model == model.id
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
                    if not thinking_block.thinking or thinking_block.thinking.strip() == "":
                        continue
                    
                    if is_same_model:
                        transformed_content.append(thinking_block)
                    else:
                        # 转换为文本块
                        transformed_content.append(
                            TextContent(
                                type="text",
                                text=thinking_block.thinking
                            )
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
                                text_signature=text_block.text_signature if is_same_model else None
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
                            tool_call.id, 
                            model, 
                            assistant_msg
                        )
                        if normalized_id != tool_call.id:
                            tool_call_id_map[tool_call.id] = normalized_id
                            normalized_tool_call.id = normalized_id
                    
                    transformed_content.append(normalized_tool_call)
                
                else:
                    # 其他类型保持不变
                    transformed_content.append(block)
            
            # 创建新的助手消息
            new_assistant_msg = AssistantMessage(
                role="assistant",
                content=transformed_content,
                api=assistant_msg.api,
                provider=assistant_msg.provider,
                model=assistant_msg.model,
                usage=assistant_msg.usage,
                stop_reason=assistant_msg.stop_reason,
                error_message=assistant_msg.error_message,
                response_id=assistant_msg.response_id,
                response_model=assistant_msg.response_model,
                timestamp=assistant_msg.timestamp
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
                                content=[TextContent(type="text", text="No result provided")],
                                is_error=True,
                                timestamp=int(time.time() * 1000)  # 当前时间戳（毫秒）
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
                block for block in assistant_msg.content 
                if block.type == "toolCall"
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
                                content=[TextContent(type="text", text="No result provided")],
                                is_error=True,
                                timestamp=int(time.time() * 1000)
                            )
                        )
                pending_tool_calls = []
                existing_tool_result_ids = set()
            result.append(msg)
        
        else:
            result.append(msg)
    
    return result


