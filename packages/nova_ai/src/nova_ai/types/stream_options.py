"""
流选项类型定义
"""

from typing import Optional, Dict, Any, Callable
from pydantic import Field
from .base_model import NovaBaseModel
from .enums import ThinkingLevel, Transport, CacheRetention


class ProviderResponse(NovaBaseModel):
    """HTTP 响应元数据"""

    status: int
    headers: Dict[str, str]


class ThinkingBudgets(NovaBaseModel):
    """各思考级别的token预算"""

    minimal: Optional[int] = None
    low: Optional[int] = None
    medium: Optional[int] = None
    high: Optional[int] = None


class StreamOptions(NovaBaseModel):
    """流式选项"""

    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    api_key: Optional[str] = None
    transport: Optional[Transport] = None
    cache_retention: Optional[CacheRetention] = None
    session_id: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    metadata: Optional[Dict[str, Any]] = None
    # 请求超时（秒），对应 OpenAI Python SDK 的 timeout 参数
    timeout: Optional[float] = None
    # 最大重试次数，对应 OpenAI Python SDK 的 max_retries 参数
    max_retries: Optional[int] = None

    # signal、on_payload、on_response 不参与序列化
    signal: Optional[Any] = Field(default=None, exclude=True)
    on_payload: Optional[Callable] = Field(default=None, exclude=True)
    on_response: Optional[Callable[[ProviderResponse, Any], None]] = Field(
        default=None, exclude=True
    )


class SimpleStreamOptions(StreamOptions):
    """简单流式选项（带推理配置）"""

    reasoning: Optional[ThinkingLevel] = None
    thinking_budgets: Optional[ThinkingBudgets] = None
