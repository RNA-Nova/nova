# model_runtime/runtime.py
"""ModelRuntime：模型与鉴权的运行时（对齐 TS ``core/model-runtime.ts``）。

本类是 nova_ai ``Models`` 集合之上的完整运行时，而非兼容壳：

- provider 通过 ``composer`` 三层合成（内置 → models.json → 扩展注册），
  内置 provider 的 auth（含 OAuth）在无覆盖时原样保留；
- 鉴权在请求时经 ``get_auth`` 解析（runtime override → stored credential →
  models.json/extension key → 环境变量链 → OAuth 刷新），
  不把 api_key / Authorization 烘焙进 Model；
- ``stream`` / ``stream_simple`` / ``complete`` / ``login`` / ``logout``
  等 Models 表面直接透传内部集合，调用方无需触碰底层 ``Models`` 实例；
- ``get_available`` / ``has_configured_auth`` 是同步快照读，
  由 ``refresh_availability``（async）或同步近似 ``_update_sync_snapshot`` 维护。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union

from nova_ai import Model, Provider, builtin_models, create_models
from nova_ai.auth.resolve import AuthResolutionOverrides
from nova_ai.gateway.store import InMemoryModelsStore
from nova_ai.signal import AbortSignal
from nova_ai.types.auth import AuthCheck, AuthResult, AuthType, CredentialInfo
from nova_ai.types.messages import AssistantMessage, Context
from nova_ai.types.stream_options import SimpleStreamOptions, StreamOptions

from nova_harness.core.config.auth.storage import AuthStorage
from nova_harness.core.config.defaults import (
    MODELS_FILE_NAME,
    MODELS_STORE_FILE_NAME,
    get_agent_dir,
)
from nova_harness.core.config.resolve import (
    clear_config_value_cache,
    get_config_value_env_var_names,
    is_command_config_value,
    is_config_value_configured,
    resolve_headers_or_throw,
)
from nova_harness.core.model.composer import (
    compose_provider,
    validate_extension_provider,
)
from nova_harness.core.model.store import FileModelsStore
from nova_harness.core.types.model import (
    ExtensionOAuthConfig,
    ModelsConfig,
    ProviderConfigInput,
)
from nova_harness.core.utils.json import strip_json_comments
from nova_harness.package.utils import is_offline_mode_enabled


class ModelRuntime:
    """模型运行时：管理内置与自定义模型，提供鉴权解析、流式调用与可用性快照。"""

    def __init__(
        self,
        auth_storage: AuthStorage,
        models_json_path: Optional[str] = None,
        *,
        models_store: Optional[Any] = None,
        allow_model_network: Optional[bool] = None,
        auth_context: Optional[Any] = None,
    ):
        self.auth_storage: AuthStorage = auth_storage
        self.models_json_path: str = models_json_path or os.path.join(
            get_agent_dir(), MODELS_FILE_NAME
        )
        self.allow_model_network: bool = (
            allow_model_network
            if allow_model_network is not None
            else not is_offline_mode_enabled()
        )
        self.load_error: Optional[str] = None

        self._config: Optional[ModelsConfig] = None
        self._builtins: Dict[str, Provider] = {
            p.id: p for p in builtin_models().get_providers()
        }
        self._extension_providers: Dict[str, ProviderConfigInput] = {}
        # 代码级配置（无法 JSON 化），与纯数据配置分开存储
        self._extension_stream_fns: Dict[str, Callable[..., Any]] = {}
        self._extension_refresh_fns: Dict[str, Callable[..., Any]] = {}
        self._extension_oauth_configs: Dict[str, ExtensionOAuthConfig] = {}
        self._composition_errors: Dict[str, str] = {}

        if models_store is None:
            models_store = (
                FileModelsStore(
                    os.path.join(
                        os.path.dirname(self.models_json_path),
                        MODELS_STORE_FILE_NAME,
                    )
                )
                if self.models_json_path
                else InMemoryModelsStore()
            )
        self._models = create_models(
            credential_store=auth_storage,
            models_store=models_store,
            auth_context=auth_context,
            model_headers_resolver=self._resolve_model_headers,
        )

        # 可用性快照（同步读取路径）
        self._configured_providers: Set[str] = set()
        self._available_snapshot: List[Model] = []
        self._auth_checks: Dict[str, Optional[AuthCheck]] = {}
        self._availability_error: Optional[str] = None
        self._availability_task: Optional[asyncio.Task] = None

        self._load_config()
        self._rebuild_providers()
        self._update_sync_snapshot()

    # ------------------------------------------------------------------
    # 配置加载与 provider 合成
    # ------------------------------------------------------------------

    def _load_config(self) -> None:
        """从 models.json 加载配置（仅 schema 校验，credential-blind）。"""
        self._config = None
        self.load_error = None
        if not self.models_json_path or not os.path.exists(self.models_json_path):
            return
        try:
            content = Path(self.models_json_path).read_text(encoding="utf-8")
            self._config = ModelsConfig.model_validate(
                json.loads(strip_json_comments(content))
            )
        except json.JSONDecodeError as e:
            self.load_error = (
                f"Failed to parse models.json: {e}\n\nFile: {self.models_json_path}"
            )
        except Exception as e:
            self.load_error = (
                f"Failed to load models.json: {e}\n\nFile: {self.models_json_path}"
            )

    def _provider_config(self, provider_id: str):
        if self._config is None:
            return None
        return self._config.providers.get(provider_id)

    def _recompose_provider(self, provider_id: str) -> None:
        """重新合成单个 provider（对齐 TS recomposeProvider）。"""
        base = self._builtins.get(provider_id)
        config = self._provider_config(provider_id)
        extension = self._extension_providers.get(provider_id)

        if base is None and config is None and extension is None:
            self._models.delete_provider(provider_id)
            self._composition_errors.pop(provider_id, None)
            return
        if base is not None and config is None and extension is None:
            # 无任何覆盖：原样使用内置 provider，保证 auth/login/stream 行为精确
            self._models.set_provider(base)
            self._composition_errors.pop(provider_id, None)
            return
        try:
            self._models.set_provider(
                compose_provider(
                    provider_id,
                    base,
                    config,
                    extension,
                    self._extension_stream_fns.get(provider_id),
                    self._extension_refresh_fns.get(provider_id),
                    self._extension_oauth_configs.get(provider_id),
                )
            )
            self._composition_errors.pop(provider_id, None)
        except Exception as exc:
            self._composition_errors[provider_id] = str(exc)
            if base is not None:
                self._models.set_provider(base)
            else:
                self._models.delete_provider(provider_id)

    def _rebuild_providers(self) -> None:
        provider_ids: Set[str] = set(self._builtins)
        if self._config is not None:
            provider_ids.update(self._config.providers)
        provider_ids.update(self._extension_providers)
        for provider_id in provider_ids:
            self._recompose_provider(provider_id)

    # ------------------------------------------------------------------
    # per-model headers 请求时解析（对齐 TS resolveConfiguredModelHeaders）
    # ------------------------------------------------------------------

    def _raw_model_headers(self, model: Model) -> Optional[Dict[str, str]]:
        """按优先级收集 per-model 原始 header 模板（不解析）。

        优先级（后者覆盖前者）：models.json model_overrides <
        models.json models[] 定义 < 扩展注册的 models[] 定义。
        """
        headers: Dict[str, str] = {}
        config = self._provider_config(model.provider)
        if config is not None:
            override = (config.model_overrides or {}).get(model.id)
            if override is not None and override.headers:
                headers.update(override.headers)
            for definition in config.models or []:
                if definition.id == model.id and definition.headers:
                    headers.update(definition.headers)
        extension = self._extension_providers.get(model.provider)
        if extension is not None:
            for definition in extension.models or []:
                if definition.id == model.id and definition.headers:
                    headers.update(definition.headers)
        return headers or None

    async def _resolve_model_headers(
        self, model: Model, env: Optional[Dict[str, str]] = None
    ) -> Optional[Dict[str, str]]:
        """nova_ai ``model_headers_resolver`` 钩子：请求时解析 per-model headers。"""
        raw = self._raw_model_headers(model)
        if raw is None:
            return None
        return resolve_headers_or_throw(
            raw, f'model "{model.provider}/{model.id}"', env
        )

    # ------------------------------------------------------------------
    # 可用性快照
    # ------------------------------------------------------------------

    def _configured_api_key(self, provider_id: str) -> Optional[str]:
        extension = self._extension_providers.get(provider_id)
        if extension is not None and extension.api_key is not None:
            return extension.api_key
        config = self._provider_config(provider_id)
        return config.api_key if config is not None else None

    def _has_configured_auth_sync(self, provider_id: str) -> bool:
        """同步近似的鉴权配置判定（stored/runtime/env/配置 key）。"""
        if self.auth_storage.has_auth(provider_id):
            return True
        raw_key = self._configured_api_key(provider_id)
        if raw_key is None:
            return False
        # 命令型配置推迟到请求时执行，检查阶段视为已配置
        if is_command_config_value(raw_key):
            return True
        return is_config_value_configured(raw_key)

    def _update_sync_snapshot(self) -> None:
        """事件循环不可用时的同步快照（构造期与同步 mutation 后使用）。"""
        configured = {
            p.id
            for p in self._models.get_providers()
            if self._has_configured_auth_sync(p.id)
        }
        self._configured_providers = configured
        self._available_snapshot = [
            m for m in self._models.get_models() if m.provider in configured
        ]

    async def _run_availability_refresh(self) -> None:
        """用 nova_ai 的 auth 链精确刷新可用性快照（含 OAuth 判定）。"""
        providers = self._models.get_providers()

        async def _check(provider: Provider):
            try:
                return provider.id, await self._models.check_auth(provider.id)
            except Exception:
                # 单个 provider 的检查失败不拖垮整体快照
                return provider.id, None

        results = await asyncio.gather(*[_check(p) for p in providers])
        self._auth_checks = dict(results)
        configured = {pid for pid, check in results if check is not None}
        self._configured_providers = configured
        self._available_snapshot = [
            m for m in self._models.get_models() if m.provider in configured
        ]
        self._availability_error = None

    def _queue_availability_refresh(
        self, after: Optional[asyncio.Task]
    ) -> asyncio.Task:
        """把一次刷新排到 ``after`` 之后（对齐 TS queueAvailabilityRefresh）。"""

        async def _chained() -> None:
            if after is not None:
                try:
                    await after
                except Exception:
                    pass
            try:
                await self._run_availability_refresh()
            except Exception as exc:
                self._availability_error = str(exc)
                raise

        task = asyncio.ensure_future(_chained())
        self._availability_task = task
        return task

    async def refresh_availability(self) -> None:
        """刷新可用性快照；并发调用合并到同一个 inflight 刷新上。"""
        task = self._availability_task
        if task is None or task.done():
            task = self._queue_availability_refresh(None)
        await task

    async def _force_refresh_availability(self) -> None:
        """mutation 专用：排在当前 inflight 刷新之后，保证读到最新状态。"""
        await self._queue_availability_refresh(self._availability_task)

    def _schedule_availability_refresh(self) -> None:
        """在事件循环内时后台排队刷新；否则保留同步近似。"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _swallow() -> None:
            try:
                await self._force_refresh_availability()
            except Exception:
                # 错误已记录到 _availability_error，后台任务不再抛出
                pass

        asyncio.ensure_future(_swallow())

    # ------------------------------------------------------------------
    # 查询（同步）
    # ------------------------------------------------------------------

    def get_error(self) -> Optional[str]:
        """汇总 models.json 加载错误与 provider 合成错误。"""
        errors: List[str] = []
        if self.load_error:
            errors.append(self.load_error)
        for provider_id, error in self._composition_errors.items():
            errors.append(f'Provider "{provider_id}": {error}')
        if self._availability_error:
            errors.append(f"Availability refresh: {self._availability_error}")
        return "\n\n".join(errors) if errors else None

    def get_all(self) -> List[Model]:
        """全部模型（内置 + models.json + 扩展注册）。"""
        return self._models.get_models()

    def get_available_snapshot(self) -> List[Model]:
        """已配置鉴权的模型（同步快照，对齐 TS getAvailableSnapshot）。"""
        return list(self._available_snapshot)

    async def get_available(self, provider_id: Optional[str] = None) -> List[Model]:
        """已配置鉴权的模型（async，对齐 TS getAvailable）。

        先合并到当前 inflight 的可用性刷新，再读快照；
        传 ``provider_id`` 时只返回该 provider 的模型。
        """
        await self.refresh_availability()
        if provider_id is None:
            return list(self._available_snapshot)
        return [m for m in self._available_snapshot if m.provider == provider_id]

    def find(self, provider: str, model_id: str) -> Optional[Model]:
        return self._models.get_model(provider, model_id)

    def has_configured_auth(self, model: Model) -> bool:
        """该模型的 provider 是否已配置鉴权（同步快照）。"""
        return model.provider in self._configured_providers

    def is_using_oauth(self, provider_id: str) -> bool:
        """该 provider 当前解析到的鉴权是否为 OAuth（快照口径）。"""
        check = self._auth_checks.get(provider_id)
        return check is not None and check.type == "oauth"

    def get_provider_auth_status(self, provider_id: str) -> Dict[str, Any]:
        """provider 鉴权状态（对齐 TS AuthStatus / configuredRequestAuthStatus）。"""
        if self.auth_storage.has_runtime_api_key(provider_id):
            return {"configured": True, "source": "runtime"}
        if self.auth_storage.has(provider_id):
            return {"configured": True, "source": "stored"}
        raw_key = self._configured_api_key(provider_id)
        if raw_key is not None:
            if is_command_config_value(raw_key):
                return {"configured": True, "source": "models_json_command"}
            env_names = get_config_value_env_var_names(raw_key)
            if env_names:
                if is_config_value_configured(raw_key):
                    return {
                        "configured": True,
                        "source": "environment",
                        "label": ", ".join(env_names),
                    }
                return {"configured": False}
            source = (
                "fallback"
                if provider_id in self._extension_providers
                and self._extension_providers[provider_id].api_key is not None
                else "models_json_key"
            )
            return {"configured": True, "source": source}
        check = self._auth_checks.get(provider_id)
        if check is not None:
            return {
                "configured": True,
                "source": "environment",
                "label": check.source,
            }
        return {"configured": False}

    def get_provider_display_name(self, provider_id: str) -> str:
        provider = self._models.get_provider(provider_id)
        return provider.name if provider is not None else provider_id

    # ------------------------------------------------------------------
    # Models 表面：provider / 模型访问
    # ------------------------------------------------------------------

    def get_providers(self) -> List[Provider]:
        return self._models.get_providers()

    def get_provider(self, provider_id: str) -> Optional[Provider]:
        return self._models.get_provider(provider_id)

    # ------------------------------------------------------------------
    # 鉴权解析（async，请求时链路）
    # ------------------------------------------------------------------

    async def get_request_auth(
        self,
        provider_or_model: Union[str, Model],
        *,
        api_key: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Optional[AuthResult]:
        """解析请求级鉴权（apiKey + headers + env），走 nova_ai 的 auth 链。

        ``api_key`` / ``env`` 为调用方覆盖（对齐 TS getAuth 的 overrides）。
        """
        overrides = None
        if api_key is not None or env is not None:
            overrides = AuthResolutionOverrides(apiKey=api_key, env=env)
        return await self._models.get_auth(provider_or_model, overrides)

    async def get_api_key(self, model: Model) -> Optional[str]:
        """解析模型的 API key（含 OAuth access token）。"""
        result = await self._models.get_auth_for_model(model)
        if result is None:
            return None
        return result.auth.get("apiKey")

    async def get_api_key_for_provider(self, provider: str) -> Optional[str]:
        """解析 provider 的 API key（含 OAuth access token）。"""
        result = await self._models.get_auth(provider)
        if result is None:
            return None
        return result.auth.get("apiKey")

    async def check_auth(self, provider_id: str) -> Optional[AuthCheck]:
        """精确检查 provider 鉴权配置（不触发网络刷新）。"""
        return await self._models.check_auth(provider_id)

    # ------------------------------------------------------------------
    # Models 表面：流式调用
    # ------------------------------------------------------------------

    def stream(
        self,
        model: Model,
        context: Context,
        options: Optional[StreamOptions] = None,
    ) -> Any:
        """流式调用（auth 在流启动时异步解析）。"""
        return self._models.stream(model, context, options)

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: Optional[SimpleStreamOptions] = None,
    ) -> Any:
        """简化流式调用。"""
        return self._models.stream_simple(model, context, options)

    async def complete(
        self,
        model: Model,
        context: Context,
        options: Optional[StreamOptions] = None,
    ) -> AssistantMessage:
        """非流式补全（收集整条流到最终结果）。"""
        return await self._models.complete(model, context, options)

    async def complete_simple(
        self,
        model: Model,
        context: Context,
        options: Optional[SimpleStreamOptions] = None,
    ) -> AssistantMessage:
        """简化非流式补全。"""
        return await self._models.complete_simple(model, context, options)

    # ------------------------------------------------------------------
    # 登录/登出（OAuth 链路）
    # ------------------------------------------------------------------

    async def login(
        self, provider_id: str, auth_type: AuthType, interaction: Any
    ) -> Any:
        """执行 provider 登录流程并持久化 credential，随后联动模型刷新。"""
        credential = await self._models.login(provider_id, auth_type, interaction)
        await self.refresh()
        return credential

    async def logout(self, provider_id: str) -> None:
        """删除已存储 credential，并重置依赖 credential 的 provider 投影。"""
        await self._models.logout(provider_id)
        self._recompose_provider(provider_id)
        self._update_sync_snapshot()
        await self.refresh()

    # ------------------------------------------------------------------
    # 扩展 provider 注册
    # ------------------------------------------------------------------

    def register_provider(
        self,
        provider_name: str,
        config: Union[ProviderConfigInput, Dict[str, Any]],
        *,
        stream_fn: Optional[Callable[..., Any]] = None,
        refresh_models_fn: Optional[Callable[..., Any]] = None,
        oauth: Optional[ExtensionOAuthConfig] = None,
    ) -> None:
        """动态注册 provider（供扩展调用）。

        stream_fn / refresh_models_fn / oauth 是代码级配置（无法 JSON 化），
        通过独立参数传入；dict 形式 config 中的 ``stream_simple`` 键也会被
        引导到 stream_fn 通道。重复注册时，已定义的字段覆盖前一次注册
        （对齐 TS 合并语义）。
        """
        if isinstance(config, dict):
            config = dict(config)
            stream_fn = stream_fn or config.pop("stream_simple", None)
            config = ProviderConfigInput.model_validate(config)

        base = self._builtins.get(provider_name)
        validate_extension_provider(
            provider_name, base, self._provider_config(provider_name), config, stream_fn
        )

        previous = self._extension_providers.get(provider_name)
        if previous is not None:
            config = previous.model_copy(update=config.model_dump(exclude_unset=True))
            if stream_fn is None:
                stream_fn = self._extension_stream_fns.get(provider_name)
            if refresh_models_fn is None:
                refresh_models_fn = self._extension_refresh_fns.get(provider_name)
            if oauth is None:
                oauth = self._extension_oauth_configs.get(provider_name)

        self._extension_providers[provider_name] = config
        if stream_fn is not None:
            self._extension_stream_fns[provider_name] = stream_fn
        else:
            self._extension_stream_fns.pop(provider_name, None)
        if refresh_models_fn is not None:
            self._extension_refresh_fns[provider_name] = refresh_models_fn
        else:
            self._extension_refresh_fns.pop(provider_name, None)
        if oauth is not None:
            self._extension_oauth_configs[provider_name] = oauth
        else:
            self._extension_oauth_configs.pop(provider_name, None)

        self._recompose_provider(provider_name)
        self._update_sync_snapshot()
        self._schedule_availability_refresh()

    def unregister_provider(self, provider_name: str) -> None:
        """注销扩展注册的 provider，恢复内置/models.json 形态。"""
        if provider_name not in self._extension_providers:
            return
        del self._extension_providers[provider_name]
        self._extension_stream_fns.pop(provider_name, None)
        self._extension_refresh_fns.pop(provider_name, None)
        self._extension_oauth_configs.pop(provider_name, None)
        self._recompose_provider(provider_name)
        self._update_sync_snapshot()
        self._schedule_availability_refresh()

    def get_registered_provider_config(
        self, provider_name: str
    ) -> Optional[ProviderConfigInput]:
        return self._extension_providers.get(provider_name)

    def get_registered_provider_ids(self) -> List[str]:
        return list(self._extension_providers)

    # ------------------------------------------------------------------
    # Runtime credential 管理
    # ------------------------------------------------------------------

    async def set_runtime_api_key(self, provider_id: str, api_key: str) -> None:
        """设置 runtime API key（不落盘），并联动快照与模型刷新。"""
        self.auth_storage.set_runtime_api_key(provider_id, api_key)
        self._update_sync_snapshot()
        await self.refresh()

    async def remove_runtime_api_key(self, provider_id: str) -> None:
        """移除 runtime API key，并联动快照与模型刷新。"""
        self.auth_storage.remove_runtime_api_key(provider_id)
        self._update_sync_snapshot()
        await self.refresh()

    async def list_credentials(self) -> List[CredentialInfo]:
        """列出全部 credential 元信息（不暴露 secret）。"""
        return await self.auth_storage.list()

    # ------------------------------------------------------------------
    # 刷新与重载
    # ------------------------------------------------------------------

    async def refresh(
        self,
        allow_network: Optional[bool] = None,
        signal: Optional[AbortSignal] = None,
    ) -> Dict[str, Any]:
        """网络刷新动态模型目录，并随后精确刷新可用性快照（对齐 TS refresh）。"""
        if allow_network is None:
            allow_network = self.allow_model_network
        result = await self._models.refresh(allow_network=allow_network, signal=signal)
        try:
            await self._force_refresh_availability()
        except Exception:
            # 快照刷新失败不影响已刷新的模型（错误已记入 _availability_error）
            pass
        return result

    async def reload_config(self) -> None:
        """完整重载：models.json + provider 重组 + 配置值缓存清理 + 网络刷新。"""
        self._load_config()
        self._rebuild_providers()
        self._update_sync_snapshot()
        clear_config_value_cache()
        await self.refresh()


__all__ = ["ModelRuntime"]
