"""endpoints_store 测试：config.toml [[environments]] 读写手术。"""

import pytest

from nova_coding_agent.executor import endpoints_store


@pytest.fixture
def store_home(tmp_path, monkeypatch):
    """假 exec-server home：endpoints_store 的全部 I/O 落临时目录。"""
    home = tmp_path / "home"
    monkeypatch.setattr(
        endpoints_store, "default_exec_server_home", lambda: str(home)
    )
    # load 走 SDK loader——SDK 的 home 解析也要指向同一临时目录
    monkeypatch.setattr(
        "nova_exec_server_client.config.default_exec_server_home", lambda: home
    )
    return home


def test_load_empty_when_no_file(store_home):
    assert endpoints_store.load_endpoints() == []


def test_register_and_load_roundtrip(store_home):
    endpoints_store.register_endpoint("gpu-01", "wss://gpu-01:8080")
    endpoints_store.register_endpoint("gpu-02", "ssh://alice@gpu-02", cwd="/data")
    loaded = endpoints_store.load_endpoints()
    assert loaded == [
        {"name": "gpu-01", "url": "wss://gpu-01:8080", "cwd": None},
        {"name": "gpu-02", "url": "ssh://alice@gpu-02", "cwd": "/data"},
    ]


def test_register_replaces_same_name(store_home):
    endpoints_store.register_endpoint("gpu", "wss://a:1")
    endpoints_store.register_endpoint("gpu", "wss://b:2")
    loaded = endpoints_store.load_endpoints()
    assert len(loaded) == 1
    assert loaded[0]["url"] == "wss://b:2"


def test_unregister_removes_only_target_block(store_home):
    endpoints_store.register_endpoint("a", "wss://a:1")
    endpoints_store.register_endpoint("b", "wss://b:2")
    assert endpoints_store.unregister_endpoint("a") is True
    loaded = endpoints_store.load_endpoints()
    assert [e["name"] for e in loaded] == ["b"]
    assert endpoints_store.unregister_endpoint("a") is False


def test_other_sections_untouched(store_home):
    """TOML 手术只动 [[environments]] 段——其余配置原样保留。"""
    home = store_home
    home.mkdir(parents=True)
    (home / "config.toml").write_text(
        'sandbox_mode = "read-only"\napproval_policy = "never"\n\ndefault_environment = "gpu"\n',
        encoding="utf-8",
    )
    endpoints_store.register_endpoint("gpu", "wss://g:1")
    text = (home / "config.toml").read_text(encoding="utf-8")
    assert 'sandbox_mode = "read-only"' in text
    assert 'approval_policy = "never"' in text
    assert 'default_environment = "gpu"' in text
    assert 'id = "gpu"' in text
    endpoints_store.unregister_endpoint("gpu")
    text = (home / "config.toml").read_text(encoding="utf-8")
    assert 'id = "gpu"' not in text
    assert 'sandbox_mode = "read-only"' in text
