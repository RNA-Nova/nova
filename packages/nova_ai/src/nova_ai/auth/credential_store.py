"""CredentialStore 内存实现（同步引擎——默认与测试路径）。

同步字典操作，无锁无链：写互斥与刷新去重是调用方（编排层）的职责，
锁拓扑见 ``docs/crosscutting/concurrency.md``。
"""

from typing import Dict, List, Optional

from nova_ai.auth import CredentialStore
from nova_protocol import Credential, CredentialInfo


class InMemoryCredentialStore(CredentialStore):
    """内存凭证存储（同步三件套）。"""

    def __init__(self) -> None:
        self._credentials: Dict[str, Credential] = {}

    def read(self, provider_id: str) -> Optional[Credential]:
        return self._credentials.get(provider_id)

    def list(self) -> List[CredentialInfo]:
        return [
            CredentialInfo(provider_id=provider_id, type=credential.type)
            for provider_id, credential in self._credentials.items()
        ]

    def write(self, provider_id: str, credential: Credential) -> None:
        self._credentials[provider_id] = credential

    def delete(self, provider_id: str) -> None:
        self._credentials.pop(provider_id, None)


__all__ = ["InMemoryCredentialStore"]
