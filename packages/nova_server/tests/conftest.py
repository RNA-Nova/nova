"""server 包测试全局环境。

物化包装（``remote_catalog``）的目录拉取在单测里一律失败——包装器走
"瞬时失败保缓存"分支（对齐 pi 的瞬态语义）。与 backend 侧 conftest 同款。
"""

import pytest


@pytest.fixture(autouse=True)
def _offline_model_catalog(monkeypatch):
    monkeypatch.setattr(
        "nova_ai.providers.remote_catalog.fetch_models_dev",
        _raise_offline,
    )


def _raise_offline(*_args, **_kwargs):
    raise RuntimeError("model catalog fetch is disabled in tests")
