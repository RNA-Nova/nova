"""Credential storage for API keys and OAuth tokens.

对齐 TypeScript ``auth-storage.ts`` + ``runtime-credentials.ts``：
实现 ``CredentialStore`` **同步引擎**协议（read/list/write/delete 四件套，
支持 api_key 与 oauth 两种 credential），并内置 runtime API key
overrides（不落盘）。写互斥与刷新去重归编排层（per-provider 锁，
见 ``docs/crosscutting/concurrency.md``），本类不含网络/长操作。

**存储层是哑 JSON 映射（pi 对位）**：读写不校验条目形态——原文进、原文出，
未知/损坏条目天然全量保留（不存在"解析失败被静默抹掉"的类目）。
校验在消费点发生（``read`` 把条目物化为类型化 Credential；
无法物化按 None 处理）。凭证类型仅两种标准形态：
``{"type": "api_key", "key", "env"?}`` 与
``{"type": "oauth", "access", "refresh", "expires", "accountId"?}``。

派生查询（鉴权状态、可用性快照）由 ``ModelRuntime`` 承担，
本类只保留其所需的两个同步判定原语：``has`` / ``has_auth``。
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from nova_ai import get_env_api_key
from nova_ai.auth import CredentialStore
from nova_protocol import (
    ApiKeyCredential,
    Credential,
    CredentialInfo,
    OAuthCredential,
    RedactedString,
)

from nova_harness.config.defaults import AUTH_FILE_NAME, get_agent_dir
from nova_harness.config.resolve import resolve_config_value
from nova_harness.config.storage import FileStorageBackend, StorageBackend


class AuthStorage(CredentialStore):
    """JSON 文件后端的 credential 存储（含 runtime overrides）。"""

    def __init__(self, storage: StorageBackend):
        self._storage = storage
        # 原始条目（dict）——不做校验，保证重写时不丢任何数据
        self._data: Dict[str, Dict[str, Any]] = {}
        self._runtime_overrides: Dict[str, str] = {}
        self._revision: Optional[str] = None
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

    def read(self, provider_id: str) -> Optional[Credential]:
        """读取已存储 credential（先经 revision 比对同步外部写入）。

        对齐 TS ``RuntimeCredentials.read``：runtime override（CLI --api-key）
        优先级最高；API key 的 ``key`` 字段按 ``resolve_config_value``
        解析（``$VAR``/``${VAR}`` 环境变量引用或 ``!cmd`` 命令），
        credential 自带的 ``env`` 参与解析；解析失败时 ``key`` 置 None
        （下游按"无 key"处理，回落环境变量链，而非把原文当 key 发出去）。
        无法物化为标准形态的条目按"未配置"处理（原文不动，可 /logout）。
        """
        self._reload_if_changed()
        runtime_key = self._runtime_overrides.get(provider_id)
        if runtime_key:
            return ApiKeyCredential(key=RedactedString(runtime_key))
        credential = self._materialize(self._data.get(provider_id))
        if credential is None:
            return None
        if credential.type == "api_key" and credential.key is not None:
            resolved_key = resolve_config_value(credential.key, credential.env)
            if resolved_key != credential.key:
                return ApiKeyCredential(
                    key=(
                        RedactedString(resolved_key)
                        if resolved_key is not None
                        else None
                    ),
                    env=credential.env,
                )
        return credential

    def list(self) -> List[CredentialInfo]:
        """列出所有 credential 元信息（含 runtime overrides）。

        类型字段直接取条目原值（pi 对位——不校验；未知类型如实透出）。
        """
        self._reload_if_changed()
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

    def write(self, provider_id: str, credential: Credential) -> None:
        """写入（新增/替换）credential 并持久化。

        全局文件锁内**重新读取**最新内容再做合并写回（多进程不互踩）；
        只有目标条目被替换，其余条目原文保留；写盘走 temp+rename 原子写
        （见 ``config/storage/backends.py``）。调用方负责 per-provider 锁
        串行（编排层不变量，见 ``docs/crosscutting/concurrency.md``）。
        """

        merged: Dict[str, Dict[str, Any]] = {}

        def _fn(current: Optional[str]) -> str:
            nonlocal merged
            merged = dict(self._parse_storage_data(current))
            merged[provider_id] = credential.model_dump(exclude_none=True)
            return json.dumps(merged, indent=2)

        self._storage.with_lock(_fn)
        # 写盘成功后才更新内存快照与版本戳（失败路径内存与文件不分叉）
        self._data = merged
        self._revision = self._storage.revision()

    def delete(self, provider_id: str) -> None:
        """删除 credential（同时清除 runtime override）。

        哑映射删除：无法解析的条目同样可删（/logout 对损坏条目也可用）。
        """

        current_data: Dict[str, Dict[str, Any]] = {}

        def _fn(current: Optional[str]) -> str:
            nonlocal current_data
            current_data = self._parse_storage_data(current)
            current_data.pop(provider_id, None)
            return json.dumps(current_data, indent=2)

        self._storage.with_lock(_fn)
        # 写盘成功后才更新内存快照与 override（失败路径不分叉）
        self._data = current_data
        self._runtime_overrides.pop(provider_id, None)
        self._revision = self._storage.revision()

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
        """Reload credentials from storage（失败时保留内存中的旧快照——pi 同款回落）。"""
        content: Optional[str] = None

        def reload_fn(current: Optional[str]) -> None:
            nonlocal content
            content = current
            return None

        try:
            self._storage.with_lock(reload_fn)
            self._data = self._parse_storage_data(content)
            self._revision = self._storage.revision()
        except Exception:
            pass

    def _reload_if_changed(self) -> None:
        """文件被外部修改时重载（revision 比对——pi ``getFileRevision`` 对位）。

        每次 read/list 先做一次 stat——跨进程写（另一 nova 实例刷新了
        凭据）必须被看见，否则 per-provider 锁内的复查读到陈旧快照。
        """
        revision = self._storage.revision()
        if revision is not None and revision != self._revision:
            self._reload()

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


__all__ = [
    "ApiKeyCredential",
    "AuthStorage",
    "OAuthCredential",
]
