"""Auth 类型定义。

对齐 TypeScript ``src/auth/types.ts``：Credential、CredentialStore、
AuthContext、AuthInteraction、ApiKeyAuth、OAuthAuth、ProviderAuth 等。

选型约定：

- 需要 JSON parse/dump 的持久化 schema（credential）→ Pydantic；
- 运行时容器与行为容器（持有 Callable）→ dataclass；
- dict 形状透传（``ModelAuth``）→ TypedDict；
- 服务接口 → Protocol。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Protocol,
    TypedDict,
    Union,
)

from pydantic import ConfigDict

from ..signal import AbortSignal
from .aliases import ProviderEnv, ProviderHeaders
from .base_model import NovaBaseModel

# ---------------------------------------------------------------------------
# 基础类型
# ---------------------------------------------------------------------------


class ModelAuth(TypedDict, total=False):
    """单次模型请求可使用的鉴权信息（进程内契约：auth 层 → api_impl 层）。"""

    api_key: str
    headers: ProviderHeaders
    base_url: str


AuthType = Literal["api_key", "oauth"]


@dataclass(frozen=True, kw_only=True)
class AuthResult:
    """解析后的 provider 鉴权结果。"""

    auth: ModelAuth
    env: Optional[ProviderEnv] = None
    source: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class AuthCheck:
    """鉴权是否已配置的轻量检查结果。"""

    type: AuthType
    source: Optional[str] = None


# ---------------------------------------------------------------------------
# Credential（auth.json 持久化 schema）
# ---------------------------------------------------------------------------


class ApiKeyCredential(NovaBaseModel):
    """存储的 API key 凭证。"""

    type: Literal["api_key"] = "api_key"
    key: Optional[str] = None
    env: Optional[ProviderEnv] = None


class OAuthCredential(NovaBaseModel):
    """存储的 OAuth 凭证。

    ``extra="allow"`` 对齐 TS ``OAuthCredentials`` 的
    ``[key: string]: unknown``：token 响应中的扩展字段（如 accountId
    之外的 provider 专有字段）在往返序列化中保留。
    """

    model_config = ConfigDict(extra="allow")

    type: Literal["oauth"] = "oauth"
    access: str = ""
    refresh: str = ""
    expires: int = 0
    accountId: Optional[str] = None


Credential = Union[ApiKeyCredential, OAuthCredential]


@dataclass(frozen=True, kw_only=True)
class CredentialInfo:
    """不暴露 secret 的 credential 元信息。"""

    provider_id: str
    type: AuthType


# ---------------------------------------------------------------------------
# AuthContext
# ---------------------------------------------------------------------------


class AuthContext(Protocol):
    """鉴权解析时可注入的环境访问抽象。"""

    async def env(self, name: str) -> Optional[str]:
        """读取环境变量。"""
        ...

    async def file_exists(self, path: str) -> bool:
        """检查文件是否存在（支持 ``~`` 开头）。"""
        ...


# ---------------------------------------------------------------------------
# CredentialStore
# ---------------------------------------------------------------------------


class CredentialStore(Protocol):
    """凭证持久化抽象。

    与 TS ``CredentialStore`` 对齐：按 ``provider_id`` 存储一个 credential，
    ``modify`` 是唯一的写路径，且按 provider id 串行化。
    """

    async def read(self, provider_id: str) -> Optional[Credential]:
        """读取已存储的 credential。"""
        ...

    async def list(self) -> List[CredentialInfo]:
        """列出所有 credential 元信息。"""
        ...

    async def modify(
        self,
        provider_id: str,
        fn: Callable[[Optional[Credential]], Awaitable[Optional[Credential]]],
    ) -> Optional[Credential]:
        """串行化读写 mutation。"""
        ...

    async def delete(self, provider_id: str) -> None:
        """删除 credential。"""
        ...


# ---------------------------------------------------------------------------
# AuthInteraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class AuthPromptOption:
    """select prompt 的选项。"""

    id: str
    label: str
    description: Optional[str] = None


@dataclass
class AuthPrompt:
    """向用户发起的交互请求。"""

    type: str
    message: str
    placeholder: Optional[str] = None
    options: Optional[List[AuthPromptOption]] = None
    signal: Optional[AbortSignal] = None


@dataclass(frozen=True, kw_only=True)
class AuthInfoLink:
    """info event 中的链接。"""

    url: str
    label: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class AuthEvent:
    """登录流程中的状态/通知事件。"""

    type: str
    message: Optional[str] = None
    url: Optional[str] = None
    instructions: Optional[str] = None
    userCode: Optional[str] = None
    verificationUri: Optional[str] = None
    verificationUriComplete: Optional[str] = None
    intervalSeconds: Optional[int] = None
    expiresInSeconds: Optional[int] = None
    links: Optional[List[AuthInfoLink]] = None


class AuthInteraction(Protocol):
    """登录流程与 UI 的交互契约。"""

    signal: Optional[AbortSignal] = None

    async def prompt(self, prompt: AuthPrompt) -> str:
        """向用户发起 prompt，返回用户输入/选择。"""
        ...

    def notify(self, event: AuthEvent) -> None:
        """通知 UI 当前登录状态。"""
        ...


# ---------------------------------------------------------------------------
# ProviderAuth
# ---------------------------------------------------------------------------


@dataclass
class ApiKeyAuth:
    """API key 鉴权定义。"""

    name: str
    resolve: Callable[
        [Dict[str, Any]],
        Awaitable[Optional[AuthResult]],
    ]
    login: Optional[Callable[[AuthInteraction], Awaitable[ApiKeyCredential]]] = None
    check: Optional[Callable[[Dict[str, Any]], Awaitable[Optional[AuthCheck]]]] = None


@dataclass
class OAuthAuth:
    """OAuth 鉴权定义。"""

    name: str
    login: Callable[[AuthInteraction], Awaitable[OAuthCredential]]
    refresh: Callable[[OAuthCredential, Optional[Any]], Awaitable[OAuthCredential]]
    to_auth: Callable[[OAuthCredential], Awaitable[ModelAuth]]
    login_label: Optional[str] = None


@dataclass
class ProviderAuth:
    """Provider 的鉴权配置。至少提供 apiKey 或 oauth 之一。"""

    api_key: Optional[ApiKeyAuth] = None
    oauth: Optional[OAuthAuth] = None


__all__ = [
    "ApiKeyAuth",
    "ApiKeyCredential",
    "AuthCheck",
    "AuthContext",
    "AuthEvent",
    "AuthInfoLink",
    "AuthInteraction",
    "AuthPrompt",
    "AuthPromptOption",
    "AuthResult",
    "AuthType",
    "Credential",
    "CredentialInfo",
    "CredentialStore",
    "ModelAuth",
    "OAuthAuth",
    "OAuthCredential",
    "ProviderAuth",
    "ProviderEnv",
    "ProviderHeaders",
]
