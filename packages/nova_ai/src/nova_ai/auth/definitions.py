"""Provider 鉴权行为定义与输入。"""

from dataclasses import dataclass
from typing import Awaitable, Callable, NotRequired, Optional, TypedDict

from nova_async import AbortSignal
from nova_protocol import (
    ApiKeyCredential,
    AuthCheck,
    AuthResult,
    ModelAuth,
    OAuthCredential,
)

from .ports import AuthContext, AuthInteraction


class ApiKeyAuthInput(TypedDict):
    """``ApiKeyAuth.resolve`` / ``check`` 的入参包（规则 10：声明不校验）。

    - ``ctx``：鉴权解析期的环境访问（环境变量读取 / 文件探测）；
    - ``credential``：已存储的 API key 凭据；``None`` = 无存储凭据，
      实现应回落到环境变量等 ambient 来源；
    - ``signal``：调用方取消信号。可选键——历史调用点可能不携带，
      实现方用 ``input.get("signal")`` 读取。
    """

    ctx: AuthContext
    credential: Optional[ApiKeyCredential]
    signal: NotRequired[Optional[AbortSignal]]


@dataclass(frozen=True, kw_only=True)
class ApiKeyAuth:
    """API key 鉴权定义（行为定义——持 Callable，规则 4 不进 Pydantic）。

    ``login`` / ``check`` 为 ``None`` 时该 provider 不支持交互登录 /
    轻量配置检查。
    """

    name: str
    resolve: Callable[
        [ApiKeyAuthInput],
        Awaitable[Optional[AuthResult]],
    ]
    login: Optional[Callable[[AuthInteraction], Awaitable[ApiKeyCredential]]] = None
    check: Optional[Callable[[ApiKeyAuthInput], Awaitable[Optional[AuthCheck]]]] = None


@dataclass(frozen=True, kw_only=True)
class OAuthAuth:
    """OAuth 鉴权定义（行为定义——持 Callable，规则 4 不进 Pydantic）。

    ``login_label``：登录方式选择器里的展示名；``None`` = 用 ``name``。
    """

    name: str
    login: Callable[[AuthInteraction], Awaitable[OAuthCredential]]
    refresh: Callable[
        [OAuthCredential, Optional[AbortSignal]], Awaitable[OAuthCredential]
    ]
    to_auth: Callable[[OAuthCredential], Awaitable[ModelAuth]]
    # 该登录方式是否由 provider 订阅套餐支撑（订阅计费/用量语义）。
    is_subscription: Optional[bool] = None
    login_label: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class ProviderAuth:
    """Provider 的鉴权配置。至少提供 api_key 或 oauth 之一（均为 None 即未配置）。"""

    api_key: Optional[ApiKeyAuth] = None
    oauth: Optional[OAuthAuth] = None


__all__ = [
    "ApiKeyAuth",
    "ApiKeyAuthInput",
    "OAuthAuth",
    "ProviderAuth",
]
