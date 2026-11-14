"""模型运行时消费接口。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Protocol, Union

from nova_protocol import Model

if TYPE_CHECKING:
    from nova_ai.gateway.models import ModelsRefreshResult
    from nova_async import AbortSignal
    from nova_protocol import AuthResult, NovaDiagnostic

    from nova_harness.model.config import ExtensionOAuthConfig


class ModelRuntimeProtocol(Protocol):
    """鉴权解析、流式调用、可用性快照与动态 provider 注册。"""

    async def get_api_key(self, model: Model) -> Optional[str]: ...
    async def get_request_auth(
        self,
        provider_or_model: Union[str, Model],
        *,
        api_key: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Optional[AuthResult]: ...
    def is_using_oauth(self, provider_id: str) -> bool: ...
    def stream_simple(self, model: Model, context: Any, options: Any = None) -> Any: ...
    def has_configured_auth(self, model: Model) -> bool: ...
    async def refresh(
        self,
        allow_network: Optional[bool] = None,
        signal: Optional[AbortSignal] = None,
    ) -> ModelsRefreshResult: ...
    async def refresh_availability(self) -> None: ...
    async def login(
        self, provider_id: str, auth_type: Any, interaction: Any
    ) -> Any: ...
    async def logout(self, provider_id: str) -> None: ...
    def register_provider(
        self,
        provider_name: str,
        config: Any,
        *,
        stream_fn: Optional[Callable[..., Any]] = None,
        refresh_models_fn: Optional[Callable[..., Any]] = None,
        oauth: Optional[ExtensionOAuthConfig] = None,
    ) -> None: ...
    def unregister_provider(self, provider_name: str) -> None: ...
    def set_diagnostic_listener(
        self, listener: Optional[Callable[[NovaDiagnostic], None]]
    ) -> None: ...
    def get_all(self) -> List[Model]: ...
    def get_available_snapshot(self) -> List[Model]: ...
    async def get_available(self, provider_id: Optional[str] = None) -> List[Model]: ...
    def find(self, provider: str, model_id: str) -> Optional[Model]: ...


__all__ = ["ModelRuntimeProtocol"]
