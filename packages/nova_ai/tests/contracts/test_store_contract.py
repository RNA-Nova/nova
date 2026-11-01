"""Protocol 接缝的契约套件（conformance——对位 codex
``file_cache_implements_models_cache_contract``）——nova_ai 实例化（内存实现）。

套件逻辑单源：``nova_ai.testkit.store_contracts``（继承契约类 + fixture
绑定实现工厂）。文件型生产实现的同考见 ``nova_harness/tests/contracts/``。
新增实现的及格线 = 在自己包的 tests/contracts/ 实例化并全绿
（testing-modeling §四.4）；行为不许因实现而分裂。
"""

import pytest

from nova_ai.auth.credential_store import InMemoryCredentialStore
from nova_ai.gateway.store import InMemoryModelsStore
from nova_ai.testkit.store_contracts import CredentialStoreContract, ModelsStoreContract


@pytest.fixture(params=[InMemoryModelsStore], ids=lambda f: f.__name__)
def models_store(request):
    return request.param()


@pytest.mark.asyncio
class TestModelsStoreContract(ModelsStoreContract):
    """InMemoryModelsStore 跑 ModelsStore 契约。"""


@pytest.fixture(params=[InMemoryCredentialStore], ids=lambda f: f.__name__)
def credential_store(request):
    return request.param()


class TestCredentialStoreContract(CredentialStoreContract):
    """InMemoryCredentialStore 跑 CredentialStore 契约。"""
