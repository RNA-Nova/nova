"""模型注册表运行时类型（``core/model/`` 域的数据契约）。"""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Union

from nova_ai import (
    Model,
    ModelCost,
    OpenAICompletionsCompat,
    OpenAIResponsesCompat,
    ThinkingLevelMap,
)
from nova_ai.types.auth import OAuthCredential
from nova_ai.types.base_model import NovaBaseModel
from pydantic import ConfigDict, Field

# 类型别名
# 注（规则 6 的知情偏离）：两个成员模型（nova_ai types/compat）全部字段
# 可选、无 Literal 判别位，判别联合需要用户 models.json 增写字段——破坏
# 存量配置的代价不接受。smart-union 下：含独有字段的 dict 因得分更高命中
# 正确成员；只含共享字段的 dict 打平落首成员，但共享字段两方同义，无数据
# 损失面。若将来 compat 增删字段使两成员出现"同键异义"，必须改判别联合。
OpenAICompat = Union[OpenAICompletionsCompat, OpenAIResponsesCompat]


class ModelDefinition(NovaBaseModel):

    model_config = ConfigDict(frozen=True)

    id: str
    name: Optional[str] = None
    api: Optional[str] = None
    base_url: Optional[str] = None
    reasoning: Optional[bool] = None
    input: Optional[List[Literal["text", "image"]]] = None
    cost: Optional[ModelCost] = None
    context_window: Optional[int] = None
    max_tokens: Optional[int] = None
    headers: Optional[Dict[str, str]] = None
    compat: Optional[OpenAICompat] = None
    thinking_level_map: Optional[ThinkingLevelMap] = None


class ModelOverride(NovaBaseModel):

    model_config = ConfigDict(frozen=True)

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

    model_config = ConfigDict(frozen=True)

    name: Optional[str] = None
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

    model_config = ConfigDict(frozen=True)

    providers: Dict[str, ProviderConfig] = Field(default_factory=dict)


class ProviderConfigInput(NovaBaseModel):
    """Input type for register_provider API.

    纯数据配置（可 JSON 化）。代码级的自定义流式函数不进本模型，
    通过 ``ModelRuntime.register_provider(..., stream_fn=...)`` 的独立参数传入。
    """

    model_config = ConfigDict(frozen=True)

    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    api: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    auth_header: Optional[bool] = None
    compat: Optional[OpenAICompat] = None
    thinking_level_map: Optional[ThinkingLevelMap] = None
    models: Optional[List[ModelDefinition]] = None


@dataclass(frozen=True)
class ExtensionOAuthConfig:
    """扩展注册的 OAuth 配置（对齐 TS ``ExtensionOAuthConfig``）。

    代码级配置（持有 Callable，不可 JSON 化），通过
    ``ModelRuntime.register_provider(..., oauth=...)`` 传入。
    """

    name: str
    # 登录流程：接收 nova_ai AuthInteraction，返回 OAuthCredential 或可校验的 dict
    login: Callable[[Any], Awaitable[Any]]
    # 刷新 token：接收过期 credential，返回新 credential
    refresh_token: Callable[[OAuthCredential], Awaitable[Any]]
    # 从 credential 提取请求用 api key
    get_api_key: Callable[[OAuthCredential], str]
    # 可选：拿到 OAuth credential 后修改模型列表（如按账号过滤）
    modify_models: Optional[Callable[[List[Model], OAuthCredential], List[Model]]] = (
        None
    )


__all__ = [
    "OpenAICompat",
    "ModelDefinition",
    "ModelOverride",
    "ProviderConfig",
    "ModelsConfig",
    "ProviderConfigInput",
    "ExtensionOAuthConfig",
]
