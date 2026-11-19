"""Nova Gateway 的请求、响应与错误词汇。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from nova_protocol import (
    CacheRetention,
    Context,
    Model,
    ModelType,
    NovaBaseModel,
    NovaErrorKind,
    ThinkingLevel,
    Transport,
)
from pydantic import ConfigDict, Field


class ModelRef(NovaBaseModel):
    """下游只能选择服务端已登记的模型，不能上传端点或凭据。"""

    model_config = ConfigDict(extra="forbid")

    provider: str
    id: str


class ThinkingBudgets(NovaBaseModel):
    """各思考级别的 token 预算；`None` 表示该级别未指定，出货时省略。"""

    minimal: Optional[int] = None
    low: Optional[int] = None
    medium: Optional[int] = None
    high: Optional[int] = None


class StreamOptions(NovaBaseModel):
    """允许跨 Gateway 边界的调用选项白名单。

    所有 Optional 字段的 `None` 都表示“未指定”，出货时省略，不等于显式
    `null`；服务端与 Provider 使用各自缺省语义。
    """

    model_config = ConfigDict(extra="forbid")

    temperature: Optional[float] = None
    # 自由负载：Provider 特定的采样扩展。
    sampling_params: Optional[Dict[str, Any]] = None
    max_tokens: Optional[int] = None
    reasoning: Optional[ThinkingLevel] = None
    cache_retention: Optional[CacheRetention] = None
    session_id: Optional[str] = None
    disable_continuation: Optional[bool] = None
    # 自由负载：调用方自定义元数据。
    metadata: Optional[Dict[str, Any]] = None
    transport: Optional[Transport] = None
    # 自由负载：Provider 特定的工具选择。
    tool_choice: Optional[Any] = None
    thinking_budgets: Optional[ThinkingBudgets] = None
    # 上游请求重试次数；缺省由 Provider 决定，不属于 SDK 本地重试。
    max_retries: Optional[int] = None
    max_retry_delay_ms: Optional[int] = None
    timeout: Optional[float] = None


class StreamRequest(NovaBaseModel):
    """原生聊天请求；`options` 缺省为全未指定。"""

    model_config = ConfigDict(extra="forbid")

    model: ModelRef
    context: Context
    options: StreamOptions = Field(default_factory=StreamOptions)


class ModelListItem(NovaBaseModel):
    """服务端允许调用的模型摘要；窗口未知时对应字段为 `None`。"""

    provider: str
    id: str
    name: str
    type: ModelType = "chat"
    reasoning: bool = False
    input_types: List[str]
    context_window: Optional[int] = None
    max_tokens: Optional[int] = None
    available: bool = True

    @classmethod
    def from_model(cls, model: Model) -> "ModelListItem":
        return cls(
            provider=model.provider,
            id=model.id,
            name=model.name,
            type="chat",
            reasoning=model.reasoning,
            input_types=list(model.input_types),
            context_window=model.context_window,
            max_tokens=model.max_tokens,
            available=True,
        )


class ModelListResponse(NovaBaseModel):
    """当前白名单过滤后的聊天模型列表。"""

    models: List[ModelListItem]


class ErrorPayload(NovaBaseModel):
    """机器可读的公开错误；分类与退避信息缺失时为 `None` 并省略。"""

    message: str
    kind: Optional[NovaErrorKind] = None
    retry_after_ms: Optional[int] = None


class ErrorResponse(NovaBaseModel):
    """原生 HTTP 错误信封，与 SSE 的错误终态分开。"""

    error: ErrorPayload


class CatalogManifest(NovaBaseModel):
    """公共目录发布物的版本、Provider 清单与逐文件 SHA-256。"""

    schema_version: int
    generated_at: str
    providers: List[str]
    files: Dict[str, str]


__all__ = [
    "CatalogManifest",
    "ErrorPayload",
    "ErrorResponse",
    "ModelListItem",
    "ModelListResponse",
    "ModelRef",
    "StreamOptions",
    "StreamRequest",
    "ThinkingBudgets",
]
