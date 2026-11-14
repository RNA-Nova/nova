"""server 包测试全局环境。

内置 provider 的远程目录拉取在单测里一律失败——两个公开站的 fetch 统一
返回 status=0（治理皮走"瞬时失败保缓存"分支，对齐 pi 的瞬态语义）。
与 harness 侧 conftest 同款。
"""

import pytest
from nova_ai.catalog_impls import ModelsDevCatalog, PiDevCatalog
from nova_ai.gateway.catalog import CatalogOutcome


@pytest.fixture(autouse=True)
def _offline_model_catalog(monkeypatch):
    async def _offline_fetch(*_args, **_kwargs):
        return CatalogOutcome(status=0)

    monkeypatch.setattr(ModelsDevCatalog, "fetch", _offline_fetch)
    monkeypatch.setattr(PiDevCatalog, "fetch", _offline_fetch)
