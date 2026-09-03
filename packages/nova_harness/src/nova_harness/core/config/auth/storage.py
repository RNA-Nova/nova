"""Credential storage for API keys and OAuth tokens.

对齐 TypeScript ``auth-storage.ts`` + ``runtime-credentials.ts``：
实现 ``CredentialStore`` 协议（按 provider id 串行化读写，支持 api_key 与
oauth 两种 credential），并内置 runtime API key overrides（不落盘）。

**存储层是哑 JSON 映射（pi 对位）**：读写不校验条目形态——原文进、原文出，
未知/损坏条目天然全量保留（不存在"解析失败被静默抹掉"的类目）。
校验在消费点发生（``read``/``modify`` 的回调入参把条目物化为类型化
Credential；无法物化按 None 处理）。凭证类型仅两种标准形态：
``{"type": "api_key", "key", "env"?}`` 与
``{"type": "oauth", "access", "refresh", "expires", "accountId"?}``。

派生查询（鉴权状态、可用性快照）由 ``ModelRuntime`` 承担，
本类只保留其所需的两个同步判定原语：``has`` / ``has_auth``。
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from nova_ai import get_env_api_key
from nova_ai.types.auth import (
    ApiKeyCredential,
    Credential,
    CredentialInfo,
    CredentialStore,
    OAuthCredential,
)
from nova_harness.core.config.defaults import AUTH_FILE_NAME, get_agent_dir
from nova_harness.core.config.resolve import resolve_config_value
from nova_harness.core.config.storage import (
    FileStorageBackend,
    StorageBackend,
)


class AuthStorage(CredentialStore):
    """JSON 文件后端的 credential 存储（含 runtime overrides）。"""

    def __init__(self, storage: StorageBackend):
        self._storage = storage
        # 原始条目（dict）——不做校验，保证重写时不丢任何数据
        self._data: Dict[str, Dict[str, Any]] = {}
        self._runtime_overrides: Dict[str, str] = {}
        self._chains: Dict[str, Any] = {}
        self._reload()

    @classmethod
    def create(
        cls, auth_path: Optional[Path] = None, timeout: float = 30.0
    ) -> "AuthStorage":
        """Create AuthStorage with file backend."""
        if auth_path is None:
            auth_path = Path(get_agent_dir()) / AUTH_FILE_NAME
        storage = FileStorageBackend(
            auth_path,
            timeout=timeout,
            file_mode=0o600,
            dir_mode=0o700,
            initial_content="{}",
        )
        return cls(storage)

    @classmethod
    def from_storage(cls, storage: StorageBackend) -> "AuthStorage":
        """Create AuthStorage from existing backend."""
        return cls(storage)

    # -----------------------------------------------------------------------
    # CredentialStore protocol
    # -----------------------------------------------------------------------

    async def read(self, provider_id: str) -> Optional[Credential]:
        """读取已存储 credential。

        对齐 TS ``RuntimeCredentials.read``：runtime override（CLI --api-key）
        优先级最高；API key 的 ``key`` 字段按 ``resolve_config_value``
        解析（``$VAR``/``${VAR}`` 环境变量引用或 ``!cmd`` 命令），
        credential 自带的 ``env`` 参与解析；解析失败时 ``key`` 置 None
        （下游按"无 key"处理，回落环境变量链，而非把原文当 key 发出去）。
        无法物化为标准形态的条目按"未配置"处理（原文不动，可 /logout）。
        """
        runtime_key = self._runtime_overrides.get(provider_id)
        if runtime_key:
            return ApiKeyCredential(key=runtime_key)
        credential = self._materialize(self._data.get(provider_id))
        if credential is None:
            return None
        if credential.type == "api_key" and credential.key is not None:
            resolved_key = resolve_config_value(credential.key, credential.env)
            if resolved_key != credential.key:
                return ApiKeyCredential(key=resolved_key, env=credential.env)
        return credential

    async def list(self) -> List[CredentialInfo]:
        """列出所有 credential 元信息（含 runtime overrides）。

        类型字段直接取条目原值（pi 对位——不校验；未知类型如实透出）。
        """
        entries = {
            provider_id: str(raw.get("type", ""))
            for provider_id, raw in self._data.items()
        }
        for provider_id in self._runtime_overrides:
            entries[provider_id] = "api_key"
        return [
            CredentialInfo(provider_id=provider_id, type=cred_type)  # type: ignore[arg-type]
            for provider_id, cred_type in entries.items()
        ]

    async def modify(
        self,
        provider_id: str,
        fn: Callable[[Optional[Credential]], Awaitable[Optional[Credential]]],
    ) -> Optional[Credential]:
        """串行化读写 mutation，并持久化到文件。

        对齐 TS：在文件锁内**重新读取**最新文件内容再做合并写回，
        多进程并发写不会互相覆盖；只有目标条目被替换，其余条目原文保留。
        """

        async def _task() -> Optional[Credential]:
            async def _atomic(content: Optional[str]):
                current_data = self._parse_storage_data(content)
                next_credential = await fn(
                    self._materialize(current_data.get(provider_id))
                )
                if next_credential is None:
                    self._data = current_data
                    return self._materialize(current_data.get(provider_id)), None

                merged = dict(current_data)
                merged[provider_id] = next_credential.model_dump(exclude_none=True)
                self._data = merged
                return next_credential, json.dumps(merged, indent=2)

            return await self._storage.with_lock_async(_atomic)

        return await self._enqueue(provider_id, _task)

    async def delete(self, provider_id: str) -> None:
        """删除 credential（同时清除 runtime override）。

        哑映射删除：无法解析的条目同样可删（/logout 对损坏条目也可用）。
        """

        async def _task() -> None:
            async def _atomic(content: Optional[str]):
                current_data = self._parse_storage_data(content)
                current_data.pop(provider_id, None)
                self._data = current_data
                self._runtime_overrides.pop(provider_id, None)
                return None, json.dumps(current_data, indent=2)

            await self._storage.with_lock_async(_atomic)

        await self._enqueue(provider_id, _task)

    def _enqueue(
        self,
        provider_id: str,
        task: Callable[[], Awaitable[Any]],
    ) -> Any:
        """按 provider id 串行化任务。"""
        previous = self._chains.get(provider_id)
        if previous is None:
            previous = asyncio.get_running_loop().create_future()
            previous.set_result(None)

        async def _run() -> Any:
            try:
                await previous
            except Exception:
                pass
            return await task()

        # 链上必须存可重复 await 的 Task：裸协程只能 await 一次，
        # 后续调用者 await 同一协程会 RuntimeError（被静默吞掉），
        # 串行化随之失效。
        next_task = asyncio.ensure_future(_run())
        self._chains[provider_id] = next_task
        return next_task

    # -----------------------------------------------------------------------
    # Runtime overrides（不落盘，对齐 TS RuntimeCredentials）
    # -----------------------------------------------------------------------

    def set_runtime_api_key(self, provider: str, api_key: str) -> None:
        """Set a runtime API key override (not persisted to disk)."""
        self._runtime_overrides[provider] = api_key

    def remove_runtime_api_key(self, provider: str) -> None:
        """Remove a runtime API key override."""
        self._runtime_overrides.pop(provider, None)

    def has_runtime_api_key(self, provider: str) -> bool:
        """Check if a runtime API key override exists (对齐 TS hasRuntimeApiKey)。"""
        return provider in self._runtime_overrides

    # -----------------------------------------------------------------------
    # 内部实现
    # -----------------------------------------------------------------------

    def _parse_storage_data(self, content: Optional[str]) -> Dict[str, Dict[str, Any]]:
        """解析存储文件为原始条目映射（不校验——哑存储，pi 对位）。"""
        if not content:
            return {}
        raw = json.loads(content)
        if not isinstance(raw, dict):
            raise ValueError(
                f"Invalid auth storage: expected an object, got {type(raw).__name__}"
            )
        return {
            provider_id: cred_data
            for provider_id, cred_data in raw.items()
            if isinstance(cred_data, dict)
        }

    def _materialize(self, raw: Optional[Dict[str, Any]]) -> Optional[Credential]:
        """把原始条目物化为类型化 Credential（消费点校验）；失败返回 None。"""
        if raw is None:
            return None
        cred_type = raw.get("type")
        try:
            if cred_type == "api_key":
                return ApiKeyCredential.model_validate(raw)
            if cred_type == "oauth":
                return OAuthCredential.model_validate(raw)
        except Exception:
            return None
        return None

    def _reload(self) -> None:
        """Reload credentials from storage（失败时保留内存中的旧快照）。"""
        content: Optional[str] = None

        def reload_fn(current: Optional[str]) -> None:
            nonlocal content
            content = current
            return None

        try:
            self._storage.with_lock(reload_fn)
            self._data = self._parse_storage_data(content)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # 同步判定原语（ModelRuntime 快照近似的数据源）
    # -----------------------------------------------------------------------

    def has(self, provider: str) -> bool:
        """Check if credentials exist for a provider in auth.json."""
        return provider in self._data

    def has_auth(self, provider: str) -> bool:
        """Check if any form of auth is configured for a provider."""
        if provider in self._runtime_overrides:
            return True
        if provider in self._data:
            return True
        return bool(get_env_api_key(provider))

    def reload(self) -> None:
        """Reload credentials from storage."""
        self._reload()


__all__ = [
    "ApiKeyCredential",
    "AuthStorage",
    "OAuthCredential",
]
