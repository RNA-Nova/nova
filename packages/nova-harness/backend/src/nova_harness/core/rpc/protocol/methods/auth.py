"""Auth 域 JSON-RPC 方法。

鉴权状态查询（getAuthStatus）、API key 登录（setApiKey）、登出（logout）
与交互式登录（login——OAuth device code / ApiKey prompt，交互经反向原语
``ui/select`` / ``ui/input`` 进行）。

写操作统一走 ``ModelRuntime`` 的联动路径（credential 变更 → 模型刷新 +
可用性快照重算），不直接摸 AuthStorage（只读状态除外）。
"""

from __future__ import annotations

from typing import Any, Dict

from nova_ai.types.auth import ApiKeyCredential, AuthType

from nova_harness.core.config.auth.interaction import UIAuthInteraction
from nova_harness.core.rpc.protocol.errors import JSONRPCError
from nova_harness.core.rpc.protocol.methods.state import ServerState
from nova_harness.core.rpc.protocol.router import MethodRegistry

_AUTH_TYPES = ("api_key", "oauth")


def register(registry: MethodRegistry, state: ServerState) -> None:
    def _session() -> Any:
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        return state.runtime.session

    def _provider(params: Dict[str, Any]) -> str:
        return params["provider"]

    async def getAuthStatus(params: Dict[str, Any]) -> Dict[str, Any]:
        """全部已存储 credential 的元信息（provider + 类型，不含密钥本体）。

        auth 存储是 agent_dir 绑定而非会话绑定：无会话时也可查询。
        """
        if state.runtime is not None:
            storage = state.runtime.services.auth_storage
        else:
            from nova_harness.core.config import AuthStorage

            storage = AuthStorage.create()
        infos = await storage.list()
        return {
            "credentials": [
                {
                    "provider": getattr(info, "provider_id", None)
                    or getattr(info, "provider", None),
                    "type": getattr(info, "type", None),
                }
                for info in infos
            ],
        }

    async def setApiKey(params: Dict[str, Any]) -> Dict[str, Any]:
        """为 provider 直接设置 API key（持久化 + 模型刷新联动）。"""
        session = _session()
        provider = _provider(params)
        api_key = params.get("apiKey") or params.get("api_key")
        if not isinstance(api_key, str) or not api_key.strip():
            raise JSONRPCError(
                JSONRPCError.INVALID_PARAMS, "Missing 'apiKey' parameter"
            )
        storage = state.runtime.services.auth_storage

        async def _set(_current: Any) -> ApiKeyCredential:
            return ApiKeyCredential(key=api_key.strip())

        await storage.modify(provider, _set)
        # 与 login 同一联动：credential 变更后刷新模型与可用性快照
        await session.model_runtime.refresh()
        return {"ok": True, "provider": provider}

    async def login(params: Dict[str, Any]) -> Dict[str, Any]:
        """交互式登录（长命令：交互经反向原语进行，结果走模型刷新联动）。

        OAuth（device code / 浏览器授权）与 ApiKey prompt 统一入口。
        能力门槛：``oauth`` 需要前端宣告 ``notify``（展示设备码/授权 URL），
        ``api_key`` 需要 ``input``（输入密钥）——未满足直接报错拒绝，
        否则 device-code 流会轮询到超时（约 15 分钟）才失败。
        """
        session = _session()
        provider = _provider(params)
        auth_type = params.get("auth_type", "oauth")
        if auth_type not in _AUTH_TYPES:
            raise JSONRPCError(
                JSONRPCError.INVALID_PARAMS,
                f"'authType' must be one of {list(_AUTH_TYPES)}",
            )
        required = "notify" if auth_type == "oauth" else "input"
        if not state.ui_context.has_capability(required):
            raise JSONRPCError(
                JSONRPCError.INVALID_PARAMS,
                f"Interactive login requires frontend capability '{required}'",
            )
        interaction = UIAuthInteraction(state.ui_context)
        credential = await session.model_runtime.login(
            provider, auth_type, interaction  # type: ignore[arg-type]
        )
        cred_type = getattr(credential, "type", None) or auth_type
        return {"ok": True, "provider": provider, "type": cred_type}

    async def logout(params: Dict[str, Any]) -> Dict[str, Any]:
        """删除 credential 并联动模型刷新/可用性快照重算。"""
        session = _session()
        provider = _provider(params)
        await session.model_runtime.logout(provider)
        return {"ok": True, "provider": provider}

    from nova_harness.core.rpc.protocol.methods import shapes as _sh

    _D = "auth"
    registry.register(
        "getAuthStatus",
        getAuthStatus,
        domain=_D,
        params_model=_sh.EmptyParams,
        result_model=_sh.GetAuthStatusResult,
    )
    registry.register(
        "setApiKey",
        setApiKey,
        domain=_D,
        params_model=_sh.SetApiKeyParams,
        result_model=_sh.ProviderResult,
    )
    registry.register(
        "login",
        login,
        domain=_D,
        params_model=_sh.LoginParams,
        result_model=_sh.LoginResult,
    )
    registry.register(
        "logout",
        logout,
        domain=_D,
        params_model=_sh.ProviderParams,
        result_model=_sh.ProviderResult,
    )
