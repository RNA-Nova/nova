"""
流选项工具函数
处理模型请求的选项配置
"""

from typing import Optional
from ..types.stream_options import StreamOptions, SimpleStreamOptions
from ..types.enums import ThinkingLevel
from ..types.model import Model


def build_base_options(
    model: Model,
    options: Optional[SimpleStreamOptions] = None,
    api_key: Optional[str] = None,
) -> StreamOptions:
    """
    构建基础流式选项

    Args:
        model: 模型对象
        options: 简单流式选项
        api_key: API密钥

    Returns:
        流式选项对象
    """
    return StreamOptions(
        temperature=options.temperature if options else None,
        max_tokens=(
            options.max_tokens or min(model.max_tokens, 32000)
            if options
            else min(model.max_tokens, 32000)
        ),
        signal=options.signal if options else None,
        api_key=api_key or (options.api_key if options else None),
        transport=options.transport if options else None,
        cache_retention=options.cache_retention if options else None,
        session_id=options.session_id if options else None,
        headers=options.headers if options else None,
        on_payload=options.on_payload if options else None,
        on_response=options.on_response if options else None,
        metadata=options.metadata if options else None,
        timeout=options.timeout if options else None,
        max_retries=options.max_retries if options else None,
    )


def clamp_reasoning(effort: Optional[ThinkingLevel]) -> Optional[ThinkingLevel]:
    """
    将xhigh思考级别降级为high

    Args:
        effort: 思考级别

    Returns:
        降级后的思考级别，如果输入为None则返回None
    """
    if effort is None:
        return None
    return ThinkingLevel.HIGH if effort == ThinkingLevel.XHIGH else effort
