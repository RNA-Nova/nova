"""
Credential storage for API keys.
Handles loading, saving credentials from auth.json.

Uses file locking to prevent race conditions when multiple instances
try to write simultaneously.
"""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Optional, Callable, Any, List, Tuple

from filelock import FileLock, Timeout
from nova_ai import get_env_api_key
from .resolve import resolve_config_value
from ..config import get_agent_dir

class ApiKeyCredential:
    """API key credential type."""
    
    def __init__(self, key: str):
        self.type = "api_key"
        self.key = key
    
    def to_dict(self) -> Dict[str, str]:
        """Convert to dictionary for serialization."""
        return {"type": self.type, "key": self.key}
    
    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "ApiKeyCredential":
        """Create from dictionary."""
        if data.get("type") != "api_key":
            raise ValueError(f"Invalid credential type: {data.get('type')}")
        return cls(data["key"])


AuthCredential = ApiKeyCredential
AuthStorageData = Dict[str, Dict[str, str]]


class AuthStorageBackend:
    """Abstract backend for auth storage."""
    
    def with_lock(self, fn: Callable[[Optional[str]], Tuple[Any, Optional[str]]]) -> Any:
        """Execute function with lock."""
        raise NotImplementedError
    
    async def with_lock_async(self, fn: Callable[[Optional[str]], Any]) -> Any:
        """Execute async function with lock."""
        raise NotImplementedError


class FileAuthStorageBackend(AuthStorageBackend):
    """File-based auth storage backend with locking."""
    
    def __init__(self, auth_path: Optional[Path] = None, timeout: float = 30.0):
        if auth_path is None:
            auth_path = Path(get_agent_dir()) / "auth.json"
        self.auth_path = Path(auth_path)
        self._lock_path = self.auth_path.with_suffix('.lock')
        self._lock = FileLock(str(self._lock_path), timeout=timeout)
    
    def _ensure_parent_dir(self) -> None:
        """Ensure parent directory exists with proper permissions."""
        parent_dir = self.auth_path.parent
        if not parent_dir.exists():
            parent_dir.mkdir(parents=True, mode=0o700)
    
    def _ensure_file_exists(self) -> None:
        """Ensure auth file exists."""
        if not self.auth_path.exists():
            with open(self.auth_path, 'w') as f:
                json.dump({}, f)
            os.chmod(self.auth_path, 0o600)
    
    @contextmanager
    def _lock_file(self):
        """Context manager for file locking with timeout handling."""
        self._ensure_parent_dir()
        self._ensure_file_exists()
        
        try:
            self._lock.acquire()
            yield
        except Timeout:
            raise RuntimeError(
                f"Could not acquire lock for {self.auth_path} within {self._lock.timeout} seconds. "
                f"Another process may be holding the lock. "
                f"Lock file: {self._lock_path}"
            ) from None
        finally:
            try:
                self._lock.release()
            except (RuntimeError, OSError):
                # Lock might not be acquired or already released
                pass
    
    def with_lock(self, fn: Callable[[Optional[str]], Tuple[Any, Optional[str]]]) -> Any:
        """Execute function with file lock."""
        with self._lock_file():
            content = None
            if self.auth_path.exists():
                with open(self.auth_path, 'r') as f:
                    content = f.read()
            
            result, next_content = fn(content)
            
            if next_content is not None:
                with open(self.auth_path, 'w') as f:
                    f.write(next_content)
                os.chmod(self.auth_path, 0o600)
            
            return result
    
    async def with_lock_async(self, fn: Callable[[Optional[str]], Any]) -> Any:
        """Execute async function with file lock."""
        # For async, we'll use a simpler approach without retries
        # Production code might want to implement proper async locking
        with self._lock_file():
            content = None
            if self.auth_path.exists():
                with open(self.auth_path, 'r') as f:
                    content = f.read()
            return await fn(content)


class InMemoryAuthStorageBackend(AuthStorageBackend):
    """In-memory auth storage backend."""
    
    def __init__(self):
        self._value: Optional[str] = None
    
    def with_lock(self, fn: Callable[[Optional[str]], Tuple[Any, Optional[str]]]) -> Any:
        """Execute function with in-memory lock."""
        result, next_value = fn(self._value)
        if next_value is not None:
            self._value = next_value
        return result
    
    async def with_lock_async(self, fn: Callable[[Optional[str]], Any]) -> Any:
        """Execute async function with in-memory lock."""
        result, next_value = await fn(self._value)
        if next_value is not None:
            self._value = next_value
        return result


class AuthStorage:
    """
    Credential storage backed by a JSON file.
    
    Handles loading, saving credentials from auth.json.
    Uses file locking to prevent race conditions when multiple instances
    try to write simultaneously.
    """
    
    def __init__(self, storage: AuthStorageBackend):
        self._storage = storage
        self._data: AuthStorageData = {}
        self._runtime_overrides: Dict[str, str] = {}
        self._fallback_resolver: Optional[Callable[[str], Optional[str]]] = None
        self._load_error: Optional[Exception] = None
        self._errors: List[Exception] = []
        self._reload()
    
    @classmethod
    def create(cls, auth_path: Optional[Path] = None, timeout: float = 30.0) -> "AuthStorage":
        """Create AuthStorage with file backend."""
        return cls(FileAuthStorageBackend(auth_path, timeout=timeout))
    
    @classmethod
    def from_storage(cls, storage: AuthStorageBackend) -> "AuthStorage":
        """Create AuthStorage from existing backend."""
        return cls(storage)
    
    @classmethod
    def in_memory(cls, data: Optional[AuthStorageData] = None) -> "AuthStorage":
        """Create in-memory AuthStorage for testing."""
        if data is None:
            data = {}
        storage = InMemoryAuthStorageBackend()
        storage.with_lock(lambda _: (None, json.dumps(data, indent=2)))
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
        def reload_fn(current: Optional[str]) -> Tuple[None, Optional[str]]:
            nonlocal content
            content = current
            return None, None
        
        content = None
        try:
            self._storage.with_lock(reload_fn)
            self._data = self._parse_storage_data(content)
            self._load_error = None
        except Exception as error:
            self._load_error = error
            self._record_error(error)
    
    def _persist_provider_change(self, provider: str, credential: Optional[AuthCredential]) -> None:
        """Persist provider credential change to storage."""
        if self._load_error:
            return
        
        def persist_fn(current: Optional[str]) -> Tuple[None, Optional[str]]:
            current_data = self._parse_storage_data(current)
            merged = dict(current_data)
            
            if credential:
                merged[provider] = credential.to_dict()
            else:
                merged.pop(provider, None)
            
            return None, json.dumps(merged, indent=2)
        
        try:
            self._storage.with_lock(persist_fn)
        except Exception as error:
            self._record_error(error)
    
    def get(self, provider: str) -> Optional[AuthCredential]:
        """Get credential for a provider."""
        cred_data = self._data.get(provider)
        if not cred_data:
            return None
        
        if cred_data.get("type") == "api_key":
            return ApiKeyCredential.from_dict(cred_data)
        
        return None
    
    def set(self, provider: str, credential: AuthCredential) -> None:
        """Set credential for a provider."""
        self._data[provider] = credential.to_dict()
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