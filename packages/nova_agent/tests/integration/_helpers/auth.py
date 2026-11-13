"""集成测试凭据链：env 优先，auth.json 兜底，OAuth 可刷新回写。

仅在显式运行 integration 时实例化；默认密封套件不会执行 fixture。
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from nova_ai import create_models_manager
from nova_ai.auth.credential_store import InMemoryCredentialStore
from nova_ai.providers.volcengine.provider import volcengine_provider
from nova_ai.utils.env import get_env_api_key
from nova_protocol import ApiKeyCredential, Credential, CredentialInfo, RedactedString
from pydantic import TypeAdapter

_AUTH_JSON_PATH = Path.home() / ".nova" / "agent" / "auth.json"
_CREDENTIAL_ADAPTER = TypeAdapter(Credential)


def _load_auth_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


class AuthJsonCredentialStore:
    """保留未知条目、只回写本实例变更的 auth.json 凭据店。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or _AUTH_JSON_PATH
        self._inner = InMemoryCredentialStore()
        self._data: Dict[str, Credential] = {}
        self._dirty: set[str] = set()
        self._deleted: set[str] = set()
        self._loaded = False

    async def ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        for provider_id, payload in _load_auth_json(self._path).items():
            try:
                credential = _CREDENTIAL_ADAPTER.validate_python(payload)
            except Exception:
                continue
            self._data[provider_id] = credential
            self._inner.write(provider_id, credential)

        env_key = get_env_api_key("volcengine")
        if env_key:
            self._inner.write(
                "volcengine", ApiKeyCredential(key=RedactedString(env_key))
            )

    def _persist(self) -> None:
        if self._path.exists():
            latest = json.loads(self._path.read_text("utf-8"))
            if not isinstance(latest, dict):
                raise TypeError("auth.json must contain a JSON object")
            payload = latest
        else:
            payload = {}
        for provider_id in self._deleted:
            payload.pop(provider_id, None)
        for provider_id in self._dirty:
            credential = self._data.get(provider_id)
            if credential is not None:
                payload[provider_id] = credential.model_dump(exclude_none=True)

        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(
            f".{self._path.name}.{os.getpid()}.{id(self)}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n", "utf-8"
            )
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)
        self._dirty.clear()
        self._deleted.clear()

    def read(self, provider_id: str) -> Optional[Credential]:
        return self._inner.read(provider_id)

    def list(self) -> List[CredentialInfo]:
        return self._inner.list()

    def write(self, provider_id: str, credential: Credential) -> None:
        self._inner.write(provider_id, credential)
        self._data[provider_id] = credential
        self._dirty.add(provider_id)
        self._deleted.discard(provider_id)
        self._persist()

    def delete(self, provider_id: str) -> None:
        self._inner.delete(provider_id)
        self._data.pop(provider_id, None)
        self._dirty.discard(provider_id)
        self._deleted.add(provider_id)
        self._persist()


def has_credential() -> bool:
    return bool(get_env_api_key("volcengine")) or "volcengine" in _load_auth_json(
        _AUTH_JSON_PATH
    )


async def build_manager():
    store = AuthJsonCredentialStore()
    await store.ensure_loaded()
    manager = create_models_manager(credential_store=store)
    manager.set_provider(volcengine_provider())
    return manager
