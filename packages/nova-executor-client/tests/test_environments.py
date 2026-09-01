"""环境注册表测试（config.py 的 [[environments]] 词汇 + environments.py 解析 +
client.from_environment 构造）

校验规则与默认解析链逐条对位 codex environment_toml.rs。
"""

import pytest

from nova_executor_client import (
    ConfigError,
    ExecutorClient,
    ExecutorConfig,
    ExecutorEnvironment,
    load_executor_config,
    resolve_environment,
)
from nova_executor_client.config import _validate  # 测试直调校验内核
from nova_executor_client.environments import LOCAL_ENVIRONMENT_ID


def _ws(id_: str = "server") -> ExecutorEnvironment:
    return ExecutorEnvironment(id=id_, url="ws://example.internal:8080")


def _stdio(id_: str = "dev-box") -> ExecutorEnvironment:
    return ExecutorEnvironment(
        id=id_, program="ssh", args=["user@host", "nova-executor", "--listen", "stdio"]
    )


class TestVocabulary:
    def test_toml_round_trip(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        (home / "config.toml").write_text(
            'default_environment = "dev-box"\n'
            "include_local = false\n"
            "[[environments]]\n"
            'id = "dev-box"\n'
            'program = "ssh"\n'
            'args = ["user@host", "nova-executor", "--listen", "stdio"]\n'
            "connect_timeout_sec = 5\n"
            "[[environments]]\n"
            'id = "server"\n'
            'url = "wss://example.internal:8443"\n',
            encoding="utf-8",
        )
        cfg = load_executor_config(executor_home=home)
        assert cfg.default_environment == "dev-box"
        assert cfg.include_local is False
        assert [e.id for e in cfg.environments] == ["dev-box", "server"]
        assert cfg.environments[0].args == [
            "user@host",
            "nova-executor",
            "--listen",
            "stdio",
        ]
        assert cfg.environments[0].connect_timeout_sec == 5
        assert cfg.environments[1].url == "wss://example.internal:8443"


class TestValidation:
    """逐条对位 codex 校验规则"""

    def test_url_and_program_are_exclusive(self):
        # 校验在 load 期（_validate）——直接构造模型不进注册表校验
        with pytest.raises(ConfigError, match="只能设置 url 或 program 之一"):
            _validate(
                {"environments": [{"id": "x", "url": "ws://a", "program": "ssh"}]},
                sources=["test"],
            )

    def test_neither_url_nor_program(self):
        with pytest.raises(ConfigError, match="只能设置 url 或 program 之一"):
            _validate({"environments": [{"id": "x"}]}, sources=["test"])

    def test_url_scheme_must_be_ws(self):
        # 校验在 load 期（_validate）——直接构造模型不进注册表校验
        with pytest.raises(ConfigError, match="ws://"):
            _validate(
                {"environments": [{"id": "x", "url": "http://a"}]}, sources=["test"]
            )

    def test_duplicate_id_rejected(self):
        # 校验在 load 期（_validate）——直接构造模型不进注册表校验
        with pytest.raises(ConfigError, match="重复"):
            _validate(
                {
                    "environments": [
                        {"id": "x", "url": "ws://a"},
                        {"id": "x", "url": "ws://b"},
                    ]
                },
                sources=["test"],
            )

    def test_id_whitespace_rejected(self):
        # 校验在 load 期（_validate）——直接构造模型不进注册表校验
        with pytest.raises(ConfigError, match="空白"):
            _validate(
                {"environments": [{"id": " x", "url": "ws://a"}]}, sources=["test"]
            )

    def test_default_must_be_registered(self):
        # 校验在 load 期（_validate）——直接构造模型不进注册表校验
        with pytest.raises(ConfigError, match="未在 environments 中注册"):
            _validate({"default_environment": "ghost"}, sources=["test"])

    def test_default_none_is_legal(self):
        cfg = _validate({"default_environment": "None"}, sources=["test"])
        assert cfg.default_environment == "None"


class TestResolve:
    def test_by_name_ws(self):
        cfg = ExecutorConfig(environments=[_ws()])
        env = resolve_environment(cfg, "server")
        assert env.kind == "ws"
        assert env.url == "ws://example.internal:8080"

    def test_by_name_stdio(self):
        cfg = ExecutorConfig(environments=[_stdio()])
        env = resolve_environment(cfg, "dev-box")
        assert env.kind == "stdio"
        assert env.program == "ssh"
        assert env.args == ("user@host", "nova-executor", "--listen", "stdio")

    def test_unknown_name_lists_available(self):
        cfg = ExecutorConfig(environments=[_ws()])
        with pytest.raises(ConfigError, match="已注册：server"):
            resolve_environment(cfg, "ghost")

    def test_default_falls_back_to_local(self):
        env = resolve_environment(ExecutorConfig())
        assert env.kind == "local"
        assert env.id == LOCAL_ENVIRONMENT_ID

    def test_explicit_default(self):
        cfg = ExecutorConfig(default_environment="server", environments=[_ws()])
        assert resolve_environment(cfg).kind == "ws"

    def test_default_none_disabled(self):
        cfg = ExecutorConfig(default_environment="none", environments=[_ws()])
        with pytest.raises(ConfigError, match="已禁用"):
            resolve_environment(cfg)

    def test_include_local_false_blocks_fallback(self):
        cfg = ExecutorConfig(include_local=False, environments=[_ws()])
        with pytest.raises(ConfigError, match="include_local=false"):
            resolve_environment(cfg)

    def test_include_local_false_blocks_explicit_local(self):
        cfg = ExecutorConfig(include_local=False, environments=[_ws()])
        with pytest.raises(ConfigError, match="include_local=false 禁用"):
            resolve_environment(cfg, "local")


class TestFromEnvironment:
    def test_ws(self):
        client = ExecutorClient.from_environment(
            resolve_environment(ExecutorConfig(environments=[_ws()]), "server")
        )
        assert isinstance(client, ExecutorClient)

    def test_stdio_maps_spawn_params(self):
        client = ExecutorClient.from_environment(
            resolve_environment(ExecutorConfig(environments=[_stdio()]), "dev-box")
        )
        assert isinstance(client, ExecutorClient)

    def test_local_default(self):
        client = ExecutorClient.from_environment(resolve_environment(ExecutorConfig()))
        assert isinstance(client, ExecutorClient)

    def test_connect_timeout_wired(self):
        endpoint = ExecutorEnvironment(
            id="s", url="ws://example.internal:1", connect_timeout_sec=3
        )
        client = ExecutorClient.from_environment(
            resolve_environment(ExecutorConfig(environments=[endpoint]), "s")
        )
        assert client._connect_timeout == 3
