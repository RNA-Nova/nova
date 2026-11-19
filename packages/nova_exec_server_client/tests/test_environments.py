"""环境注册表测试（config.py 的 [[environments]] 词汇 + environments.py 解析 +
client.from_environment 构造）

校验规则与默认解析链逐条对位 codex environment_toml.rs。
"""

from pathlib import Path

import pytest

from nova_exec_server_client import (
    ConfigError,
    ExecutorClient,
    ExecutorConfig,
    ExecutorEnvironment,
    load_executor_config,
    resolve_environment,
)
from nova_exec_server_client.config import (  # 测试直调校验内核
    _validate,
    validate_runtime_environment,
)
from nova_exec_server_client.environments import LOCAL_ENVIRONMENT_ID


def _ws(id_: str = "server") -> ExecutorEnvironment:
    return ExecutorEnvironment(id=id_, url="ws://example.internal:8080")


def _stdio(id_: str = "dev-box") -> ExecutorEnvironment:
    return ExecutorEnvironment(
        id=id_,
        program="ssh",
        args=["user@host", "nova-exec-server", "--listen", "stdio"],
    )


class TestVocabulary:
    def test_toml_round_trip(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        # connect_timeout 依附 url（codex 交叉校验）——故配在 WS 条目；
        # stdio 条目配 initialize_timeout（两种传输都合法的握手预算）
        (home / "config.toml").write_text(
            'default_environment = "dev-box"\n'
            "include_local = false\n"
            "[[environments]]\n"
            'id = "dev-box"\n'
            'program = "ssh"\n'
            'args = ["user@host", "nova-exec-server", "--listen", "stdio"]\n'
            "initialize_timeout_sec = 5\n"
            "[[environments]]\n"
            'id = "server"\n'
            'url = "wss://example.internal:8443"\n'
            "connect_timeout_sec = 8\n",
            encoding="utf-8",
        )
        cfg = load_executor_config(executor_home=home)
        assert cfg.default_environment == "dev-box"
        assert cfg.include_local is False
        assert [e.id for e in cfg.environments] == ["dev-box", "server"]
        assert cfg.environments[0].args == [
            "user@host",
            "nova-exec-server",
            "--listen",
            "stdio",
        ]
        assert cfg.environments[0].initialize_timeout_sec == 5
        assert cfg.environments[1].url == "wss://example.internal:8443"
        assert cfg.environments[1].connect_timeout_sec == 8


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

    def test_reserved_ids_rejected(self):
        """保留字（对位 codex）：内建 local 精确匹配；"none" 忽略大小写"""
        for reserved in ("local", "none", "None", "NONE", "nOnE"):
            with pytest.raises(ConfigError, match="保留字"):
                _validate(
                    {"environments": [{"id": reserved, "url": "ws://a"}]},
                    sources=["test"],
                )
        # "Local" 非保留字（codex 只精确拒 "local"）——合法
        cfg = _validate(
            {"environments": [{"id": "Local", "url": "ws://a"}]}, sources=["test"]
        )
        assert cfg.environments[0].id == "Local"

    def test_id_charset_ascii_only(self):
        """字符集（对位 codex）：ASCII 字母数字 + '-' + '_'"""
        for bad in ("dev box", "dev✓box", "dev.box", "dev/box"):
            with pytest.raises(ConfigError, match="ASCII"):
                _validate(
                    {"environments": [{"id": bad, "url": "ws://a"}]},
                    sources=["test"],
                )
        cfg = _validate(
            {"environments": [{"id": "dev-box_2", "url": "ws://a"}]},
            sources=["test"],
        )
        assert cfg.environments[0].id == "dev-box_2"

    def test_id_length_measured_in_utf8_bytes(self):
        """长度按 UTF-8 字节（对位 Rust `str::len()`）——64 字符含一个双字节
        字符即 65 字节：codex 先报超长（长度校验在字符集之前）"""
        with pytest.raises(ConfigError, match="超长"):
            _validate(
                {"environments": [{"id": "a" * 65, "url": "ws://a"}]},
                sources=["test"],
            )
        # 63 ASCII + "é"（两字节）= 64 字符 / 65 字节 → 报超长而非字符集
        with pytest.raises(ConfigError, match="超长"):
            _validate(
                {"environments": [{"id": "a" * 63 + "é", "url": "ws://a"}]},
                sources=["test"],
            )
        cfg = _validate(
            {"environments": [{"id": "a" * 64, "url": "ws://a"}]}, sources=["test"]
        )
        assert len(cfg.environments[0].id) == 64

    def test_default_local_legal_when_include_local(self):
        """include_local 时内建 local 预置进候选集（对位 codex
        new_with_config_dir）——`default_environment = "local"` 合法"""
        cfg = _validate({"default_environment": "local"}, sources=["test"])
        assert cfg.default_environment == "local"

    def test_default_local_rejected_when_local_disabled(self):
        with pytest.raises(ConfigError, match="未在 environments 中注册"):
            _validate(
                {"default_environment": "local", "include_local": False},
                sources=["test"],
            )

    def test_args_env_cwd_require_program(self):
        """交叉校验（对位 codex parse_environment_toml）：args/env/cwd 依附
        program——WS 环境携带任一即拒"""
        for extra in (
            {"args": ["x"]},
            {"env": {"A": "1"}},
            {"cwd": "/tmp"},
        ):
            with pytest.raises(ConfigError, match="需要设置 program"):
                _validate(
                    {"environments": [{"id": "x", "url": "ws://a", **extra}]},
                    sources=["test"],
                )

    def test_connect_timeout_requires_url(self):
        """connect_timeout 依附 url（codex：stdio 无连接预算——connect 与
        initialize 是两笔独立预算，stdio 只有握手预算）"""
        with pytest.raises(ConfigError, match="connect_timeout_sec 需要设置 url"):
            _validate(
                {
                    "environments": [
                        {"id": "x", "program": "ssh", "connect_timeout_sec": 5}
                    ]
                },
                sources=["test"],
            )

    def test_initialize_timeout_legal_on_both_kinds(self):
        """initialize_timeout 两种传输都合法（对位 codex：WebSocketUrl 与
        StdioCommand 都携带 initialize_timeout）"""
        cfg = _validate(
            {
                "environments": [
                    {"id": "w", "url": "ws://a", "initialize_timeout_sec": 3},
                    {"id": "s", "program": "ssh", "initialize_timeout_sec": 7},
                ]
            },
            sources=["test"],
        )
        assert cfg.environments[0].initialize_timeout_sec == 3
        assert cfg.environments[1].initialize_timeout_sec == 7


class TestRuntimeValidationSplit:
    """装载期全量校验与运行时注册校验的分层（字符集/字节长是配置文件
    命名仪式，运行时动态注册不应用——executor manager 以 canonical_url
    作环境键的场景）"""

    def test_runtime_accepts_url_shaped_id(self):
        """运行时注册放行 URL 形 id（结构合法：url 二选一 + scheme 合规）"""
        env = validate_runtime_environment(
            ExecutorEnvironment(id="wss://gpu-01:8080", url="wss://gpu-01:8080")
        )
        assert env.id == "wss://gpu-01:8080"

    def test_load_rejects_url_shaped_id(self):
        """同一 id 走装载期校验被字符集仪式拒收（codex validate_environment_id
        只管配置文件条目）"""
        with pytest.raises(ConfigError, match="ASCII"):
            _validate(
                {
                    "environments": [
                        {"id": "wss://gpu-01:8080", "url": "wss://gpu-01:8080"}
                    ]
                },
                sources=["test"],
            )

    def test_runtime_still_rejects_reserved_and_structure_violations(self):
        """运行时仍强制保留字与结构不变量（防 local/none 撞车与 assert 崩）"""
        with pytest.raises(ConfigError, match="保留字"):
            validate_runtime_environment(ExecutorEnvironment(id="NONE", url="ws://a"))
        with pytest.raises(ConfigError, match="只能设置 url 或 program 之一"):
            validate_runtime_environment(ExecutorEnvironment(id="x"))


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
        assert env.args == ("user@host", "nova-exec-server", "--listen", "stdio")

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

    def test_default_stripped_before_lookup(self):
        """解析对位 codex normalize_default_environment_id：trim 后判定——
        装载期校验按 trim 后成员资格放行，解析同样 trim（旧行为拿原串
        查注册表，带空白的 default 校验过了却解析不到）"""
        cfg = ExecutorConfig(default_environment="  dev-box  ", environments=[_stdio()])
        assert resolve_environment(cfg).id == "dev-box"

    def test_default_blank_after_strip_rejected(self):
        cfg = ExecutorConfig(default_environment="   ", environments=[_ws()])
        with pytest.raises(ConfigError, match="不能为空串"):
            resolve_environment(cfg)

    def test_initialize_timeout_mapped_to_resolved(self):
        """ExecutorEnvironment.initialize_timeout_sec 经 _to_resolved 进
        ResolvedEnvironment（与 connect_timeout 分账的握手预算）"""
        ws = ExecutorEnvironment(
            id="w", url="ws://example.internal:1", initialize_timeout_sec=3
        )
        env = resolve_environment(ExecutorConfig(environments=[ws]), "w")
        assert env.kind == "ws"
        assert env.initialize_timeout_sec == 3
        stdio = ExecutorEnvironment(id="s", program="ssh", initialize_timeout_sec=7)
        env = resolve_environment(ExecutorConfig(environments=[stdio]), "s")
        assert env.kind == "stdio"
        assert env.initialize_timeout_sec == 7
        # 未配置 → None（客户端缺省预算）
        assert resolve_environment(ExecutorConfig()).initialize_timeout_sec is None


class TestStdioCwd:
    """stdio 相对 cwd 按配置文件目录归一（对位 codex normalize_stdio_cwd）"""

    def test_relative_cwd_resolved_against_user_config_dir(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        (home / "config.toml").write_text(
            "[[environments]]\n"
            'id = "dev-box"\n'
            'program = "ssh"\n'
            'cwd = "workspace"\n',
            encoding="utf-8",
        )
        cfg = load_executor_config(executor_home=home)
        assert cfg.environments[0].cwd == str(home / "workspace")

    def test_relative_cwd_resolved_against_project_config_dir(self, tmp_path):
        """project 层注册表整体覆盖 user 层——相对 cwd 按 project 配置文件
        所在目录（`.nova/`）解析"""
        home = tmp_path / "home"
        home.mkdir()
        (home / "config.toml").write_text(
            "[[environments]]\n"
            'id = "user-box"\n'
            'program = "ssh"\n'
            'cwd = "user-ws"\n',
            encoding="utf-8",
        )
        project = tmp_path / "proj"
        (project / ".nova").mkdir(parents=True)
        (project / ".nova" / "settings.json").write_text(
            '{"exec-server": {"environments": ['
            '{"id": "proj-box", "program": "ssh", "cwd": "proj-ws"}]}}',
            encoding="utf-8",
        )
        cfg = load_executor_config(project, project_trusted=True, executor_home=home)
        assert [e.id for e in cfg.environments] == ["proj-box"]
        assert cfg.environments[0].cwd == str(project / ".nova" / "proj-ws")

    def test_absolute_cwd_passes_through(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        # 平台感知绝对路径：POSIX `/var/work`，Windows 当前盘 `C:\var\work`
        # （`Path("/").resolve()` 在 Windows 取当前盘根——`/var/work` 无盘符
        # 在 Windows 非绝对，硬编码会让该用例平台依赖）
        abs_cwd = str(Path("/").resolve() / "var" / "work")
        (home / "config.toml").write_text(
            "[[environments]]\n" 'id = "dev-box"\n' 'program = "ssh"\n'
            # TOML 字面串（单引号）——Windows 反斜杠不经转义
            f"cwd = '{abs_cwd}'\n",
            encoding="utf-8",
        )
        cfg = load_executor_config(executor_home=home)
        assert cfg.environments[0].cwd == abs_cwd

    def test_relative_cwd_without_config_dir_rejected(self):
        """无来源目录（运行时直构/_validate 直调）的相对 cwd → ConfigError
        （codex：cwd must be absolute）"""
        with pytest.raises(ConfigError, match="必须是绝对路径"):
            _validate(
                {"environments": [{"id": "x", "program": "ssh", "cwd": "ws"}]},
                sources=["test"],
            )

    def test_relative_cwd_with_explicit_config_dir(self, tmp_path):
        cfg = _validate(
            {"environments": [{"id": "x", "program": "ssh", "cwd": "ws"}]},
            sources=["test"],
            config_dir=tmp_path,
        )
        assert cfg.environments[0].cwd == str(tmp_path / "ws")


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
