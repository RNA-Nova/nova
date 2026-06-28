"""
Credential storage for API keys.

Handles loading, saving credentials from auth.json.
Uses file locking to prevent race conditions when multiple instances
try to write simultaneously.
"""

import json
from pathlib import Path
from typing import Callable, Dict, List, Optional

from nova_ai import get_env_api_key
from nova_ai.types.base_model import NovaBaseModel

from nova_harness.core.config.defaults import get_agent_dir
from nova_harness.core.config.resolve import resolve_config_value
from nova_harness.core.config.storage import (
    FileStorageBackend,
    InMemoryStorageBackend,
    StorageBackend,
)


class ApiKeyCredential(NovaBaseModel):
    """API key credential type."""

    type: str = "api_key"
    key: str

    def to_dict(self) -> Dict[str, str]:
        """Convert to dictionary for serialization."""
        return {"type": self.type, "key": self.key}

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "ApiKeyCredential":
        """Create from dictionary."""
        if data.get("type") != "api_key":
            raise ValueError(f"Invalid credential type: {data.get('type')}")
        return cls(key=data["key"])


AuthStorageData = Dict[str, Dict[str, str]]


class AuthStorage:
    """
    Credential storage backed by a JSON file.

    Handles loading, saving credentials from auth.json.
    Uses file locking to prevent race conditions when multiple instances
    try to write simultaneously.
    """

    def __init__(self, storage: StorageBackend):
        self._storage = storage
        self._data: AuthStorageData = {}
        self._runtime_overrides: Dict[str, str] = {}
        self._fallback_resolver: Optional[Callable[[str], Optional[str]]] = None
        self._load_error: Optional[Exception] = None
        self._errors: List[Exception] = []
        self._reload()

    @classmethod
    def create(
        cls, auth_path: Optional[Path] = None, timeout: float = 30.0
    ) -> "AuthStorage":
        """Create AuthStorage with file backend."""
        if auth_path is None:
            auth_path = Path(get_agent_dir()) / "auth.json"
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

    @classmethod
    def in_memory(cls, data: Optional[AuthStorageData] = None) -> "AuthStorage":
        """Create in-memory AuthStorage for testing."""
        if data is None:
            data = {}
        storage = InMemoryStorageBackend(json.dumps(data, indent=2))
        return cls.from_storage(storage)

    def set_runtime_api_key(self, provider: str, api_key: str) -> None:
        """
        Set a runtime API key override (not persisted to disk).
        Used for CLI --api-key flag.
        """
        self._runtime_overrides[provider] = api_key

    def remove_runtime_api_key(self, provider: str) -> None:
        """Remove a runtime API key override."""
        self._runtime_overrides.pop(provider, None)

    def set_fallback_resolver(self, resolver: Callable[[str], Optional[str]]) -> None:
        """
        Set a fallback resolver for API keys not found in auth.json or env vars.
        Used for custom provider keys from models.json.
        """
        self._fallback_resolver = resolver

    def _record_error(self, error: Exception) -> None:
        """Record an error for later retrieval."""
        if not isinstance(error, Exception):
            error = Exception(str(error))
        self._errors.append(error)

    def _parse_storage_data(self, content: Optional[str]) -> AuthStorageData:
        """Parse storage data from JSON string."""
        if not content:
            return {}
        return json.loads(content)

    def _reload(self) -> None:
        """Reload credentials from storage."""
        content: Optional[str] = None

        def reload_fn(current: Optional[str]) -> None:
            nonlocal content
            content = current
            return None

        try:
            self._storage.with_lock(reload_fn)
            self._data = self._parse_storage_data(content)
            self._load_error = None
        except Exception as error:
            self._load_error = error
            self._record_error(error)

    def _persist_provider_change(
        self, provider: str, credential: Optional[ApiKeyCredential]
    ) -> None:
        """Persist provider credential change to storage."""
        if self._load_error:
            return

        def persist_fn(current: Optional[str]) -> str:
            current_data = self._parse_storage_data(current)
            merged = dict(current_data)

            if credential:
                merged[provider] = credential.model_dump()
            else:
                merged.pop(provider, None)

            return json.dumps(merged, indent=2)

        try:
            self._storage.with_lock(persist_fn)
        except Exception as error:
            self._record_error(error)

    def get(self, provider: str) -> Optional[ApiKeyCredential]:
        """Get credential for a provider."""
        cred_data = self._data.get(provider)
        if not cred_data:
            return None

        if cred_data.get("type") == "api_key":
            return ApiKeyCredential.model_validate(cred_data)

        return None

    def set(self, provider: str, credential: ApiKeyCredential) -> None:
        """Set credential for a provider."""
        self._data[provider] = credential.model_dump()
        self._persist_provider_change(provider, credential)

    def remove(self, provider: str) -> None:
        """Remove credential for a provider."""
        self._data.pop(provider, None)
        self._persist_provider_change(provider, None)

    def list(self) -> List[str]:
        """List all providers with credentials."""
        return list(self._data.keys())

    def has(self, provider: str) -> bool:
        """Check if credentials exist for a provider in auth.json."""
        return provider in self._data

    def has_auth(self, provider: str) -> bool:
        """
        Check if any form of auth is configured for a provider.
        """
        if provider in self._runtime_overrides:
            return True
        if provider in self._data:
            return True
        if get_env_api_key(provider):
            return True
        if self._fallback_resolver and self._fallback_resolver(provider):
            return True
        return False

    def get_all(self) -> AuthStorageData:
        """Get all credentials."""
        return dict(self._data)

    def drain_errors(self) -> List[Exception]:
        """Get and clear recorded errors."""
        drained = list(self._errors)
        self._errors.clear()
        return drained

    async def get_api_key(self, provider_id: str) -> Optional[str]:
        """
        Get API key for a provider.

        Priority:
        1. Runtime override (CLI --api-key)
        2. API key from auth.json
        3. Environment variable
        4. Fallback resolver (models.json custom providers)
        """
        # Runtime override takes highest priority
        runtime_key = self._runtime_overrides.get(provider_id)
        if runtime_key:
            return runtime_key

        cred = self.get(provider_id)

        if cred and cred.type == "api_key":
            return resolve_config_value(cred.key)

        # Fall back to environment variable
        env_key = get_env_api_key(provider_id)
        if env_key:
            return env_key

        # Fall back to custom resolver (e.g., models.json custom providers)
        if self._fallback_resolver:
            return self._fallback_resolver(provider_id)

        return None

    def reload(self) -> None:
        """Reload credentials from storage."""
        self._reload()


__all__ = [
    "ApiKeyCredential",
    "AuthStorage",
    "AuthStorageData",
]
