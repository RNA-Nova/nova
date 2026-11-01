"""集成测试凭据与 manager 装配（对位 pi ``test/oauth.ts``）。

env 优先、``~/.nova/agent/auth.json`` 兜底；OAuth 过期经 provider 自家
refresh 链刷新并回写（0600）。

**仅 integration 使用**——触碰真实用户凭据是集成测试的特许例外
（默认套件的密封条款不覆盖这里）。
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from nova_protocol import Credential, CredentialInfo
from pydantic import TypeAdapter

from nova_ai.auth.credential_store import InMemoryCredentialStore
from nova_ai.gateway.models import ModelsManager, create_models_manager
from nova_ai.providers.all import builtin_providers
from nova_ai.utils.env import get_env_api_key

AUTH_JSON_PATH = Path.home() / ".nova" / "agent" / "auth.json"

_credential_adapter = TypeAdapter(Credential)


def _load_auth_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


class AuthJsonCredentialStore:
    """``CredentialStore`` 实现：以 ``auth.json`` 为后端的薄封装。

    行为语义（按 provider 串行化等）复用 ``InMemoryCredentialStore``；
    ``modify``/``delete`` 后整体回写（0600），让 OAuth 刷新结果对
    后续运行持续有效（pi 的测试凭据同款回写）。
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or AUTH_JSON_PATH
        self._inner = InMemoryCredentialStore()
        self._data: Dict[str, Credential] = {}
        self._loaded = False

    async def ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        for provider_id, payload in _load_auth_json(self._path).items():
            try:
                credential = _credential_adapter.validate_python(payload)
            except Exception:
                continue  # 畸形条目跳过，不拖垮整份凭据
            self._data[provider_id] = credential
            self._inner.write(provider_id, credential)

    def _persist(self) -> None:
        payload = {
            pid: cred.model_dump(exclude_none=True) for pid, cred in self._data.items()
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, indent=2) + "\n", "utf-8")
        os.chmod(self._path, 0o600)

    def read(self, provider_id: str) -> Optional[Credential]:
        return self._inner.read(provider_id)

    def list(self) -> List[CredentialInfo]:
        return self._inner.list()

    def write(self, provider_id: str, credential: Credential) -> None:
        self._inner.write(provider_id, credential)
        self._data[provider_id] = credential
        self._persist()

    def delete(self, provider_id: str) -> None:
        self._inner.delete(provider_id)
        self._data.pop(provider_id, None)
        self._persist()


def has_credential(provider_id: str) -> bool:
    """provider 是否有可用凭据（采集期同步判定：env 链或 auth.json 持有）。"""
    if get_env_api_key(provider_id):
        return True
    return provider_id in _load_auth_json(AUTH_JSON_PATH)


async def build_manager() -> ModelsManager:
    """装配带真实凭据链的 manager（auth.json 凭据店 + 全内置 provider）。"""
    store = AuthJsonCredentialStore()
    await store.ensure_loaded()
    manager = create_models_manager(credential_store=store)
    for provider in builtin_providers():
        manager.set_provider(provider)
    return manager
