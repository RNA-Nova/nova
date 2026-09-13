"""Auth 类型定义。

对齐 TypeScript ``src/auth/types.ts``：Credential、CredentialStore、
AuthContext、AuthInteraction、ApiKeyAuth、OAuthAuth、ProviderAuth 等。

选型约定：

- 需要 JSON parse/dump 的持久化 schema（credential）→ Pydantic；
- 运行时容器与行为容器（持有 Callable）→ dataclass；
- dict 形状透传（``ModelAuth`` / ``ApiKeyAuthInput``）→ TypedDict；
- 服务接口 → Protocol。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    Annotated,
    Awaitable,
    Callable,
    List,
    Literal,
    NotRequired,
    Optional,
    Protocol,
    TypedDict,
    Union,
)

from pydantic import ConfigDict, Field

from .aliases import ProviderEnv, ProviderHeaders
from .base_model import NovaBaseModel
from .signal import AbortSignal

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
    """解析后的 provider 鉴权结果。

    - ``env``：随鉴权附带的 provider 级环境变量；``None`` = 无附带 env；
    - ``source``：鉴权来源描述（环境变量名 / "stored credential" / "OAuth"
      等，用于 UI 展示与日志）；``None`` = 来源未知。
    """

    auth: ModelAuth
    env: Optional[ProviderEnv] = None
    source: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class AuthCheck:
    """鉴权是否已配置的轻量检查结果。

    ``source``：配置来源描述；``None`` = 来源未知。
    """

    type: AuthType
    source: Optional[str] = None


# ---------------------------------------------------------------------------
# Credential（auth.json 持久化 schema）
# ---------------------------------------------------------------------------


class ApiKeyCredential(NovaBaseModel):
    """存储的 API key 凭证。

    - ``key``：API key；``None`` = 未设置（仅有 env 覆盖的条目）；
    - ``env``：随凭证持久化的 provider 级环境变量；``None`` = 无。
    """

    type: Literal["api_key"] = "api_key"
    key: Optional[str] = None
    env: Optional[ProviderEnv] = None


class OAuthCredential(NovaBaseModel):
    """存储的 OAuth 凭证。

    ``extra="allow"`` 对齐 TS ``OAuthCredentials`` 的
    ``[key: string]: unknown``：token 响应中的扩展字段（如 accountId
    之外的 provider 专有字段）在往返序列化中保留。

    - ``access`` / ``refresh``：访问/刷新令牌；空串 = 未持有；
    - ``expires``：访问令牌过期的 Unix 毫秒时间戳；``0`` = 未知（按已过期处理）；
    - ``accountId``：provider 账号 id。落盘词汇沿用 TS auth.json 的
      camelCase 键名——持久化格式一个字节不变优先于 Python 命名风格
      （改名会让 ``model_dump`` 写出 ``account_id``，破坏存量 auth.json）。
    """

    model_config = ConfigDict(extra="allow")

    type: Literal["oauth"] = "oauth"
    access: str = ""
    refresh: str = ""
    expires: int = 0
    accountId: Optional[str] = None


# 判别键 ``type``（规则 6：auth.json 读入走 model_validate，显式判别）
Credential = Annotated[
    Union[ApiKeyCredential, OAuthCredential],
    Field(discriminator="type"),
]


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
    """select prompt 的选项。

    ``description``：选项的补充说明；``None`` = 无说明。
    """

    id: str
    label: str
    description: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class AuthPrompt:
    """向用户发起的交互请求。

    ``type`` 已知词汇：``select``（``options`` 选择器）/ ``secret``（遮蔽
    输入）/ ``text`` / ``manual_code``（授权码粘贴框）；未识别类型由交互
    实现按明文输入框兜底（开放词汇，宿主可扩展）。

    - ``placeholder``：输入框占位文本；``None`` = 无占位；
    - ``options``：``select`` 类型的选项列表；其余类型为 ``None``。
    """

    type: str
    message: str
    placeholder: Optional[str] = None
    options: Optional[List[AuthPromptOption]] = None


@dataclass(frozen=True, kw_only=True)
class AuthInfoLink:
    """info event 中的链接。"""

    url: str
    label: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class AuthEvent:
    """登录流程中的状态/通知事件。

    ``type`` 已知词汇：``auth_url``（浏览器授权地址，``url`` +
    ``instructions``）/ ``device_code``（设备码展示，``user_code`` +
    ``verification_uri*``）/ ``info``（``message`` + ``links``）/
    ``progress``（``message``）；未识别类型由交互实现按进度提示兜底
    （开放词汇，宿主可扩展）。

    全部载荷字段按 ``type`` 取用；``None`` = 本事件不携带该载荷。
    """

    type: str
    message: Optional[str] = None
    url: Optional[str] = None
    instructions: Optional[str] = None
    user_code: Optional[str] = None
    verification_uri: Optional[str] = None
    verification_uri_complete: Optional[str] = None
    interval_seconds: Optional[float] = None
    expires_in_seconds: Optional[float] = None
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

    async def acquire_authorization_code(self, request: "AuthorizationRequest") -> str:
        """获取授权码（浏览器回调流的"收货"高阶操作）。

        渠道装配与竞速仲裁归宿主：本地一次性监听 / 宿主监听 / 手动粘贴框，
        任选与降级由实现按部署拓扑裁决。流程层只声明需求。

        返回授权码；用户取消抛 ``LoginCancelledError``，超时抛 ``TimeoutError``。
        """
        ...


class LoginCancelledError(Exception):
    """登录流程被取消（用户 Esc / 宿主取消 / 会话中止）。

    共享取消词汇：交互实现抛出，流程与 RPC 层据此优雅收尾
    （停轮询、关监听、清框）。统一取消语义见 docs/data-modeling.md 第七节。
    """


# ---------------------------------------------------------------------------
# ProviderAuth
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class AuthorizationRequest:
    """授权码获取请求（浏览器回调流的收货需求）。

    渠道装配（本地监听 / 宿主监听 / 手动粘贴）与竞速仲裁归宿主实现；
    流程层只声明需求，不感知部署拓扑。

    字段缺席语义：
    - ``manual_prompt`` 为 None 时不挂手动粘贴兜底框（纯自动通道）；
    - ``success_html`` / ``error_html`` 为空串时收货方用自带默认页。
    """

    auth_url: str
    state: str
    redirect_port: int
    redirect_path: str = "/auth/callback"
    timeout_seconds: float = 300.0
    manual_prompt: Optional[AuthPrompt] = None
    success_html: str = ""
    error_html: str = ""


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
    login_label: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class ProviderAuth:
    """Provider 的鉴权配置。至少提供 api_key 或 oauth 之一（均为 None 即未配置）。"""

    api_key: Optional[ApiKeyAuth] = None
    oauth: Optional[OAuthAuth] = None


__all__ = [
    "ApiKeyAuth",
    "ApiKeyAuthInput",
    "AuthorizationRequest",
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
    "LoginCancelledError",
    "CredentialInfo",
    "CredentialStore",
    "ModelAuth",
    "OAuthAuth",
    "OAuthCredential",
    "ProviderAuth",
]
