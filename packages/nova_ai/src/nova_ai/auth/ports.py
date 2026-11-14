"""鉴权宿主端口与流程取消异常。"""

from typing import Optional, Protocol

from nova_async import AbortSignal
from nova_protocol import (
    AuthEvent,
    AuthorizationRequest,
    AuthPrompt,
    Credential,
    CredentialInfo,
)


class AuthContext(Protocol):
    """鉴权解析时可注入的环境访问抽象。"""

    async def env(self, name: str) -> Optional[str]:
        """读取环境变量。"""
        ...

    async def file_exists(self, path: str) -> bool:
        """检查文件是否存在（支持 ``~`` 开头）。"""
        ...


class CredentialStore(Protocol):
    """凭证持久化抽象（**同步引擎**——codex ``storage.rs`` 形状）。

    按 ``provider_id`` 存储一个 credential。四件套全部**同步、瞬时、无
    signal 参数**——实现内不得含网络/长操作（编排：OAuth 刷新去重、复查、
    重试，永远住调用方的 manager 层；锁拓扑与判别测试见
    ``docs/crosscutting/concurrency.md``）。同步无 await 点 ⇒ 取消在结构
    上打不断写入，store 整体不在取消面内。
    """

    def read(self, provider_id: str) -> Optional[Credential]:
        """读取已存储的 credential。"""
        ...

    def list(self) -> list[CredentialInfo]:
        """列出所有 credential 元信息。"""
        ...

    def write(self, provider_id: str, credential: Credential) -> None:
        """写入（新增/替换）provider 的 credential。"""
        ...

    def delete(self, provider_id: str) -> None:
        """删除 provider 的 credential。"""
        ...


class AuthInteraction(Protocol):
    """登录流程与 UI 的交互契约。"""

    signal: Optional[AbortSignal] = None

    async def prompt(self, prompt: AuthPrompt) -> str:
        """向用户发起 prompt，返回用户输入/选择。"""
        ...

    def notify(self, event: AuthEvent) -> None:
        """通知 UI 当前登录状态。"""
        ...

    async def acquire_authorization_code(self, request: AuthorizationRequest) -> str:
        """获取授权码（浏览器回调流的"收货"高阶操作）。

        渠道装配与竞速仲裁归宿主：本地一次性监听 / 宿主监听 / 手动粘贴框，
        任选与降级由实现按部署拓扑裁决。流程层只声明需求。

        返回授权码；用户取消抛 ``LoginCancelledError``，超时抛 ``TimeoutError``。
        """
        ...


class LoginCancelledError(Exception):
    """登录流程被取消（用户 Esc / 宿主取消 / 会话中止）。

    共享取消词汇：交互实现抛出，流程与 RPC 层据此优雅收尾
    （停轮询、关监听、清框）。统一取消语义见 docs/crosscutting/data-modeling.md 第七节。
    """


__all__ = [
    "AuthContext",
    "AuthInteraction",
    "CredentialStore",
    "LoginCancelledError",
]
