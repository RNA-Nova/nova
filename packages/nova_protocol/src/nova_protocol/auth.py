"""Auth 公共数据词汇。

只保留需要跨组件共享的持久化 schema、值对象与交互数据。鉴权行为定义、
Callable 配置和宿主 Protocol 归 ``nova_ai.auth``。

选型约定：

- 需要 JSON parse/dump 的持久化 schema（credential）→ Pydantic；
- 跨组件运行时值对象 → frozen dataclass；
- dict 形状透传（``ModelAuth``）→ TypedDict。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, List, Literal, Optional, Tuple, TypedDict, Union

from pydantic import ConfigDict, Field
from pydantic_core import core_schema

from .aliases import ProviderEnv, ProviderHeaders
from .base_model import NovaBaseModel


class RedactedString(str):
    """repr 脱敏的 str 子类（codex ``RedactedString`` 对位——值层脱敏）。

    **repr（调试通道）恒为 ``<redacted>``**；**str（使用通道）即原值**——
    拼 header / 序列化 / 落盘照常，使用点零改动。防的是"顺手打印模型 /
    dump 产物 / 异常栈捕获"这类 repr 路径；``print(裸值)`` 是有意取值的
    使用通道，不设防（codex 的 Debug/Display 同构）。
    """

    def __repr__(self) -> str:
        return "<redacted>"

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: object, handler: object
    ) -> core_schema.CoreSchema:
        """pydantic 接入：校验时把输入 str 包装为本类型（其余按 str 处理）。"""
        return core_schema.no_info_after_validator_function(
            cls, core_schema.str_schema()
        )


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

    # auth 含 api_key——不进 dataclass 默认 repr（security 纪律：含密结构
    # 白名单 repr；env/source 可显示）
    auth: ModelAuth = field(repr=False)
    env: Optional[ProviderEnv] = None
    source: Optional[str] = None


@dataclass(frozen=True, kw_only=True)
class AuthCheck:
    """鉴权是否已配置的轻量检查结果。

    ``source``：配置来源描述；``None`` = 来源未知。
    """

    type: AuthType
    source: Optional[str] = None


class ApiKeyCredential(NovaBaseModel):
    """存储的 API key 凭证。

    - ``key``：API key（``RedactedString``——repr 脱敏）；``None`` = 未设置
      （仅有 env 覆盖的条目）；
    - ``env``：随凭证持久化的 provider 级环境变量（值可能含密——repr 隐藏）。
    """

    type: Literal["api_key"] = "api_key"
    key: Optional[RedactedString] = None
    env: Optional[ProviderEnv] = Field(default=None, repr=False)


class OAuthCredential(NovaBaseModel):
    """存储的 OAuth 凭证。

    ``extra="allow"`` 对齐 TS ``OAuthCredentials`` 的
    ``[key: string]: unknown``：token 响应中的扩展字段（如 accountId
    之外的 provider 专有字段）在往返序列化中保留。

    - ``access`` / ``refresh``：访问/刷新令牌（``RedactedString``——repr
      脱敏）；空串 = 未持有；
    - ``expires``：访问令牌过期的 Unix 毫秒时间戳；``0`` = 未知（按已过期处理）；
    - ``accountId``：provider 账号 id。落盘词汇沿用 TS auth.json 的
      camelCase 键名——持久化格式一个字节不变优先于 Python 命名风格
      （改名会让 ``model_dump`` 写出 ``account_id``，破坏存量 auth.json）。
    """

    model_config = ConfigDict(extra="allow")

    type: Literal["oauth"] = "oauth"
    access: RedactedString = RedactedString("")
    refresh: RedactedString = RedactedString("")
    expires: int = 0
    accountId: Optional[str] = None

    def __repr_args__(self) -> List[Tuple[Optional[str], Any]]:
        """repr 白名单（``extra="allow"`` 的扩展字段可能藏第二个 token——
        只露三个安全字段，其余一律不显示）。

        返回 pydantic ``ReprArgs`` 形状：``(字段名, 值)`` 对列表——
        tuple 里的 ``Any`` 是诚实的（repr 参数值可任意），容器形状收窄声明。
        """
        return [
            ("type", self.type),
            ("expires", self.expires),
            ("accountId", self.accountId),
        ]


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
    ``verification_uri``）/ ``info``（``message`` + ``links``）/
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


@dataclass(frozen=True, kw_only=True)
class AuthorizationRequest:
    """授权码获取请求（浏览器回调流的收货需求）。

    渠道装配（本地监听 / 宿主监听 / 手动粘贴）与竞速仲裁归宿主实现；
    流程层只声明需求，不感知部署拓扑。

    字段缺席语义：
    - ``manual_prompt`` 为 None 时不挂手动粘贴兜底框（纯自动通道）；
    - ``success_html`` / ``error_html`` 为空串时收货方自带默认页。
    """

    auth_url: str
    state: str
    redirect_port: int
    redirect_path: str = "/auth/callback"
    timeout_seconds: float = 300.0
    manual_prompt: Optional[AuthPrompt] = None
    success_html: str = ""
    error_html: str = ""


__all__ = [
    "ApiKeyCredential",
    "AuthCheck",
    "AuthEvent",
    "AuthInfoLink",
    "AuthPrompt",
    "AuthPromptOption",
    "AuthResult",
    "AuthType",
    "AuthorizationRequest",
    "Credential",
    "CredentialInfo",
    "ModelAuth",
    "OAuthCredential",
    "RedactedString",
]
