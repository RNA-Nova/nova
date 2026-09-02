"""
Copilot 特定的工具函数
用于处理 GitHub Copilot 请求的头部信息
"""

from typing import Dict, List, Literal

from ..types.messages import Message


def infer_copilot_initiator(messages: List[Message]) -> Literal["user", "agent"]:
    """
    推断 Copilot 请求的发起者

    Copilot 期望 X-Initiator 头部指示请求是用户发起还是代理发起
    （例如：在助手/工具消息后的后续请求）

    Args:
        messages: 消息列表

    Returns:
        "user" 或 "agent"
    """
    if not messages:
        return "user"

    last = messages[-1]
    # 如果最后一条消息不是用户消息，则是代理发起
    return "agent" if last.role != "user" else "user"


def has_copilot_vision_input(messages: List[Message]) -> bool:
    """
    检查消息中是否包含图像输入

    Copilot 在发送图像时需要 Copilot-Vision-Request 头部

    Args:
        messages: 消息列表

    Returns:
        是否包含图像输入
    """
    for msg in messages:
        # 检查用户消息中的图像
        if msg.role == "user" and isinstance(msg.content, list):
            for content in msg.content:
                if hasattr(content, "type") and content.type == "image":
                    return True

        # 检查工具结果中的图像
        elif msg.role == "toolResult" and isinstance(msg.content, list):
            for content in msg.content:
                if hasattr(content, "type") and content.type == "image":
                    return True

    return False


def build_copilot_dynamic_headers(
    messages: List[Message], has_images: bool
) -> Dict[str, str]:
    """
    构建 Copilot 动态请求头部

    Args:
        messages: 消息列表
        has_images: 是否包含图像

    Returns:
        Copilot 请求头部字典
    """
    headers = {
        "X-Initiator": infer_copilot_initiator(messages),
        "Openai-Intent": "conversation-edits",
    }

    if has_images:
        headers["Copilot-Vision-Request"] = "true"

    return headers


# 备用：直接检查消息中的图像而不需要外部参数
def build_copilot_headers_from_messages(messages: List[Message]) -> Dict[str, str]:
    """
    直接从消息构建 Copilot 请求头部

    Args:
        messages: 消息列表

    Returns:
        Copilot 请求头部字典
    """
    return build_copilot_dynamic_headers(
        messages=messages, has_images=has_copilot_vision_input(messages)
    )
