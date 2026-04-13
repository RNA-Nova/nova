# model_registry/types.py
"""
数据层 - 所有 dataclass 定义与配置结构
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Literal, Callable, Union

from mashumaro.mixins.json import DataClassJSONMixin
from mashumaro.config import BaseConfig

from nova_ai import (
    AssistantMessageEventStream,
    Context,
    Model,
    ModelCost,
    OpenAICompletionsCompat,
    OpenAIResponsesCompat,
    SimpleStreamOptions,
)

# 类型别名
OpenAICompat = Union[OpenAICompletionsCompat, OpenAIResponsesCompat]


@dataclass
class ModelDefinition(DataClassJSONMixin):
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

    class Config(BaseConfig):
        serialize_by_alias = True
        deserialize_by_alias = True


@dataclass
class ModelOverride(DataClassJSONMixin):
    name: Optional[str] = None
    reasoning: Optional[bool] = None
    input: Optional[List[Literal["text", "image"]]] = None
    cost: Optional[ModelCost] = None
    context_window: Optional[int] = None
    max_tokens: Optional[int] = None
    headers: Optional[Dict[str, str]] = None
    compat: Optional[OpenAICompat] = None


@dataclass
class ProviderConfig(DataClassJSONMixin):
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    api: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    auth_header: Optional[bool] = None
    models: Optional[List[ModelDefinition]] = None
    model_overrides: Optional[Dict[str, ModelOverride]] = None

    class Config(BaseConfig):
        serialize_by_alias = True
        deserialize_by_alias = True


@dataclass
class ModelsConfig(DataClassJSONMixin):
    providers: Dict[str, ProviderConfig] = field(default_factory=dict)


@dataclass
class ProviderOverride:
    base_url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    api_key: Optional[str] = None


@dataclass
class CustomModelsResult:
    models: List[Model]
    overrides: Dict[str, ProviderOverride]
    model_overrides: Dict[str, Dict[str, ModelOverride]]
    error: Optional[str] = None


@dataclass
class ProviderConfigInput:
    """Input type for register_provider API."""
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    api: Optional[str] = None
    stream_simple: Optional[
        Callable[[Model, Context, Optional[SimpleStreamOptions]], AssistantMessageEventStream]
    ] = None
    headers: Optional[Dict[str, str]] = None
    auth_header: Optional[bool] = None
    models: Optional[List[ModelDefinition]] = None