"""Auth 域 JSON-RPC 方法。

鉴权状态查询（getAuthStatus）、API key 登录（setApiKey）、登出（logout）
与交互式登录（login——OAuth device code / ApiKey prompt，交互经反向原语
``ui/select`` / ``ui/input`` 进行）。

写操作统一走 ``ModelRuntime`` 的联动路径（credential 变更 → 模型刷新 +
可用性快照重算），不直接摸 AuthStorage（只读状态除外）。
"""

from __future__ import annotations

from typing import Any

from nova_ai.types.auth import ApiKeyCredential

from nova_harness.core.config.auth.interaction import UIAuthInteraction
from nova_harness.core.rpc.protocol.errors import JSONRPCError
from nova_harness.core.rpc.protocol.methods import shapes as _sh
from nova_harness.core.rpc.protocol.methods.state import ServerState
from nova_harness.core.rpc.protocol.router import MethodRegistry

_D = "auth"


def register(registry: MethodRegistry, state: ServerState) -> None:
    def _session() -> Any:
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        return state.runtime.session

    async def getAuthStatus(params: _sh.EmptyParams) -> _sh.GetAuthStatusResult:
        """全部已存储 credential 的元信息（provider + 类型，不含密钥本体）。

        auth 存储是 agent_dir 绑定而非会话绑定：无会话时也可查询。
        """
        if state.runtime is not None:
            storage = state.runtime.services.auth_storage
        else:
            from nova_harness.core.config import AuthStorage

            storage = AuthStorage.create()
        infos = await storage.list()
        return _sh.GetAuthStatusResult(
            credentials=[
                _sh.CredentialInfo(
                    provider=getattr(info, "provider_id", None)
                    or getattr(info, "provider", None),
                    type=getattr(info, "type", None),
                )
                for info in infos
            ],
        )

    async def setApiKey(params: _sh.SetApiKeyParams) -> _sh.ProviderResult:
        """为 provider 直接设置 API key（持久化 + 模型刷新联动）。"""
        session = _session()
        api_key = params.api_key
        if api_key is None or not api_key.strip():
            raise JSONRPCError(
                JSONRPCError.INVALID_PARAMS, "Missing 'apiKey' parameter"
            )
        storage = state.runtime.services.auth_storage

        async def _set(_current: Any) -> ApiKeyCredential:
            return ApiKeyCredential(key=api_key.strip())

        await storage.modify(params.provider, _set)
        # 与 login 同一联动：credential 变更后刷新模型与可用性快照
        await session.model_runtime.refresh()
        return _sh.ProviderResult(ok=True, provider=params.provider)

    async def login(params: _sh.LoginParams) -> _sh.LoginResult:
        """交互式登录（长命令：交互经反向原语进行，结果走模型刷新联动）。

        OAuth（device code / 浏览器授权）与 ApiKey prompt 统一入口。
        能力门槛：``oauth`` 需要前端宣告 ``notify``（展示设备码/授权 URL），
        ``api_key`` 需要 ``input``（输入密钥）——未满足直接报错拒绝，
        否则 device-code 流会轮询到超时（约 15 分钟）才失败。
        """
        session = _session()
        # auth_type 的取值范围由 Literal 校验在分派层把关
        required = "notify" if params.auth_type == "oauth" else "input"
        if not state.ui_context.has_capability(required):
            raise JSONRPCError(
                JSONRPCError.INVALID_PARAMS,
                f"Interactive login requires frontend capability '{required}'",
            )
        interaction = UIAuthInteraction(state.ui_context)
        credential = await session.model_runtime.login(
            params.provider, params.auth_type, interaction  # type: ignore[arg-type]
        )
        cred_type = getattr(credential, "type", None) or params.auth_type
        return _sh.LoginResult(ok=True, provider=params.provider, type=cred_type)

    async def logout(params: _sh.ProviderParams) -> _sh.ProviderResult:
        """删除 credential 并联动模型刷新/可用性快照重算。"""
        session = _session()
        await session.model_runtime.logout(params.provider)
        return _sh.ProviderResult(ok=True, provider=params.provider)

    registry.register("getAuthStatus", getAuthStatus, domain=_D)
    registry.register("setApiKey", setApiKey, domain=_D)
    registry.register("login", login, domain=_D)
    registry.register("logout", logout, domain=_D)
