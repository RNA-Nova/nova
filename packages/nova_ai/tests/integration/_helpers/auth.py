"""集成测试凭据与 manager 装配（对位 pi ``test/oauth.ts`` 与其 e2e 调用点）。

凭据解析序（对齐 pi e2e 的 ``getE2EApiKey``）：**env key 优先**（pi 在调用点
``getEnvApiKey`` 直取），``~/.nova/agent/auth.json`` 的 OAuth 兜底（过期经
provider 自家 refresh 链刷新并回写 0600）。env key 以 ``ApiKeyCredential``
身份写入**内存**凭据店——与生产链语义兼容（stored api_key 是一等凭据，
无 refresh 可死），不回写 auth.json。

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
    """``auth.json`` 凭据店：只回写本实例修改的条目。

    未识别/未来版本条目原样保留；写前重读并合并最新文件，最后经临时文件
    原子替换（0600）。畸形 JSON 不允许被当成空文件覆盖。
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or AUTH_JSON_PATH
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
                credential = _credential_adapter.validate_python(payload)
            except Exception:
                continue  # 不认识的条目不可执行，但原始数据会在回写时保留
            self._data[provider_id] = credential
            self._inner.write(provider_id, credential)
        # env key 只驻内存，不落盘；陈旧 OAuth 不再抢在显式 env key 前面。
        from nova_protocol import ApiKeyCredential, RedactedString

        from nova_ai.providers.all import builtin_providers

        for provider in builtin_providers():
            env_key = get_env_api_key(provider.id)
            if env_key:
                self._inner.write(
                    provider.id, ApiKeyCredential(key=RedactedString(env_key))
                )

    def _persist(self) -> None:
        if self._path.exists():
            # 与读取侧不同：写侧不得把损坏/ unreadable 文件归一成 {}。
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
