"""
模型注册表类型。

对应原 `nova_harness.model_registry.types`。
"""

from typing import Callable, Dict, List, Literal, Optional, Union

from nova_ai import (
    AssistantMessageEventStream,
    Context,
    Model,
    ModelCost,
    OpenAICompletionsCompat,
    OpenAIResponsesCompat,
    SimpleStreamOptions,
    ThinkingLevelMap,
)
from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field

# 类型别名
OpenAICompat = Union[OpenAICompletionsCompat, OpenAIResponsesCompat]


class ModelDefinition(NovaBaseModel):

    id: str
    name: Optional[str] = None
    api: Optional[str] = None
    reasoning: Optional[bool] = None
    input: Optional[List[Literal["text", "image"]]] = None
    cost: Optional[ModelCost] = None
    context_window: Optional[int] = None
    max_tokens: Optional[int] = None
    headers: Optional[Dict[str, str]] = None
    compat: Optional[OpenAICompat] = None
    thinking_level_map: Optional[ThinkingLevelMap] = None


class ModelOverride(NovaBaseModel):

    name: Optional[str] = None
    reasoning: Optional[bool] = None
    input: Optional[List[Literal["text", "image"]]] = None
    cost: Optional[ModelCost] = None
    context_window: Optional[int] = None
    max_tokens: Optional[int] = None
    headers: Optional[Dict[str, str]] = None
    compat: Optional[OpenAICompat] = None
    thinking_level_map: Optional[ThinkingLevelMap] = None


class ProviderConfig(NovaBaseModel):

    base_url: Optional[str] = None
    api_key: Optional[str] = None
    api: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    auth_header: Optional[bool] = None
    compat: Optional[OpenAICompat] = None
    thinking_level_map: Optional[ThinkingLevelMap] = None
    models: Optional[List[ModelDefinition]] = None
    model_overrides: Optional[Dict[str, ModelOverride]] = None


class ModelsConfig(NovaBaseModel):

    providers: Dict[str, ProviderConfig] = Field(default_factory=dict)


class ProviderOverride(NovaBaseModel):

    base_url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    api_key: Optional[str] = None
    compat: Optional[OpenAICompat] = None
    thinking_level_map: Optional[ThinkingLevelMap] = None


class CustomModelsResult(NovaBaseModel):

    models: List[Model]
    overrides: Dict[str, ProviderOverride]
    model_overrides: Dict[str, Dict[str, ModelOverride]]
    error: Optional[str] = None


class ProviderConfigInput(NovaBaseModel):
    """Input type for register_provider API."""

    base_url: Optional[str] = None
    api_key: Optional[str] = None
    api: Optional[str] = None
    stream_simple: Optional[
        Callable[
            [Model, Context, Optional[SimpleStreamOptions]], AssistantMessageEventStream
        ]
    ] = None
    headers: Optional[Dict[str, str]] = None
    auth_header: Optional[bool] = None
    compat: Optional[OpenAICompat] = None
    thinking_level_map: Optional[ThinkingLevelMap] = None
    models: Optional[List[ModelDefinition]] = None


__all__ = [
    "OpenAICompat",
    "ModelDefinition",
    "ModelOverride",
    "ProviderConfig",
    "ModelsConfig",
    "ProviderOverride",
    "CustomModelsResult",
    "ProviderConfigInput",
]
