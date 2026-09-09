"""harness 测试全局环境。

物化包装（``remote_catalog``）的目录拉取在单测里一律失败——包装器走
"瞬时失败保缓存"分支（对齐 pi 的瞬态语义），内置目录保持基线种子；
需要验证动态目录的用例自带假拉取器或显式 mock。包管理（npm/二进制
下载）等真实网络路径不受影响。
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
