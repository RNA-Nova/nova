"""Protocol 接缝的契约套件——harness 实例化（文件型实现，生产正主）。

套件逻辑单源：``nova_ai.testkit.store_contracts``（codex storage_tests.rs
的 #[path] 实例化对位）。真文件经 ``tmp_path``（真实性阶梯第一条——
tempdir 上的真 FileStore）；内存实现的同考见 ``nova_ai/tests/contracts/``。
"""

import pytest
from nova_ai.testkit.store_contracts import CredentialStoreContract, ModelsStoreContract

from nova_harness.config.auth.storage import AuthStorage
from nova_harness.model.store import FileModelsStore


@pytest.fixture(params=[FileModelsStore], ids=lambda f: f.__name__)
def models_store(request, tmp_path):
    return request.param(str(tmp_path / "models.json"))


@pytest.mark.asyncio
class TestFileModelsStoreContract(ModelsStoreContract):
    """FileModelsStore（JSON 文件 + 原子写）跑 ModelsStore 契约。"""


@pytest.fixture(params=[AuthStorage], ids=lambda f: f.__name__)
def credential_store(request, tmp_path):
    return request.param.create(tmp_path / "auth.json")


class TestFileCredentialStoreContract(CredentialStoreContract):
    """AuthStorage（JSON 文件后端）跑 CredentialStore 契约。"""
