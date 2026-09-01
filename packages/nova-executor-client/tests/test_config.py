"""executor 配置根 loader 测试（config.py）

覆盖：分层合并（codex merge.rs 语义：表深合并、列表/标量整体覆盖）、
trust 门（project 层仅在 project_trusted=True 时读取）、未知键
warn-and-ignore、坏文件响亮报错、executor home 覆盖链。
"""

import json
import logging

import pytest

from nova_executor_client import (
    ApprovalPolicy,
    ConfigError,
    ExecutorConfig,
    SandboxMode,
    load_executor_config,
)
from nova_executor_client.config import NOVA_EXECUTOR_HOME_ENV
from nova_executor_client.protocol import NetworkMode


@pytest.fixture
def home(tmp_path):
    d = tmp_path / "executor-home"
    d.mkdir()
    return d


@pytest.fixture
def project(tmp_path):
    d = tmp_path / "proj"
    (d / ".nova").mkdir(parents=True)
    return d


def _write_project(project, executor_section: dict):
    (project / ".nova" / "settings.json").write_text(
        json.dumps({"executor": executor_section}), encoding="utf-8"
    )


class TestDefaults:
    def test_empty_when_no_files(self, home):
        cfg = load_executor_config(executor_home=home)
        assert cfg == ExecutorConfig()
        assert cfg.sandbox_mode is None
        assert cfg.network_proxy is None
        assert cfg.approval_policy is ApprovalPolicy.ON_REQUEST

    def test_missing_home_dir_is_empty_layer(self, tmp_path):
        cfg = load_executor_config(executor_home=tmp_path / "nonexistent")
        assert cfg == ExecutorConfig()


class TestUserLayer:
    def test_reads_toml(self, home):
        (home / "config.toml").write_text(
            'sandbox_mode = "workspace-write"\n'
            'approval_policy = "never"\n'
            "[sandbox_workspace_write]\n"
            'writable_roots = ["/data", "/scratch"]\n'
            "network_access = true\n",
            encoding="utf-8",
        )
        cfg = load_executor_config(executor_home=home)
        assert cfg.sandbox_mode is SandboxMode.WORKSPACE_WRITE
        assert cfg.approval_policy is ApprovalPolicy.NEVER
        assert cfg.sandbox_workspace_write.writable_roots == ["/data", "/scratch"]
        assert cfg.sandbox_workspace_write.network_access is True

    def test_malformed_toml_raises_with_path(self, home):
        bad = home / "config.toml"
        bad.write_text("not = [valid", encoding="utf-8")
        with pytest.raises(ConfigError, match=str(bad)):
            load_executor_config(executor_home=home)

    def test_env_override(self, home, monkeypatch):
        (home / "config.toml").write_text('sandbox_mode = "read-only"\n')
        monkeypatch.setenv(NOVA_EXECUTOR_HOME_ENV, str(home))
        cfg = load_executor_config()
        assert cfg.sandbox_mode is SandboxMode.READ_ONLY

    def test_explicit_home_beats_env(self, home, tmp_path, monkeypatch):
        (home / "config.toml").write_text('sandbox_mode = "read-only"\n')
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.setenv(NOVA_EXECUTOR_HOME_ENV, str(other))
        cfg = load_executor_config(executor_home=home)
        assert cfg.sandbox_mode is SandboxMode.READ_ONLY


class TestProjectLayerTrustGate:
    def test_untrusted_project_layer_is_not_read(self, home, project):
        _write_project(project, {"sandbox_mode": "read-only"})
        cfg = load_executor_config(project, project_trusted=False, executor_home=home)
        assert cfg.sandbox_mode is None  # project 层被门控，user 层为空

    def test_trusted_project_layer_merges(self, home, project):
        _write_project(project, {"sandbox_mode": "read-only"})
        cfg = load_executor_config(project, project_trusted=True, executor_home=home)
        assert cfg.sandbox_mode is SandboxMode.READ_ONLY

    def test_project_section_must_be_object(self, home, project):
        (project / ".nova" / "settings.json").write_text(
            json.dumps({"executor": "oops"}), encoding="utf-8"
        )
        with pytest.raises(ConfigError, match="executor"):
            load_executor_config(project, project_trusted=True, executor_home=home)

    def test_malformed_project_json_raises(self, home, project):
        (project / ".nova" / "settings.json").write_text("{bad", encoding="utf-8")
        with pytest.raises(ConfigError):
            load_executor_config(project, project_trusted=True, executor_home=home)

    def test_project_without_executor_section_is_empty_layer(self, home, project):
        (project / ".nova" / "settings.json").write_text(
            json.dumps({"model": "x"}), encoding="utf-8"
        )
        cfg = load_executor_config(project, project_trusted=True, executor_home=home)
        assert cfg == ExecutorConfig()


class TestMergeSemantics:
    """对位 codex merge.rs：表深合并；列表/标量整体覆盖（不追加）"""

    def test_scalar_overridden_by_project(self, home, project):
        (home / "config.toml").write_text('sandbox_mode = "workspace-write"\n')
        _write_project(project, {"sandbox_mode": "read-only"})
        cfg = load_executor_config(project, project_trusted=True, executor_home=home)
        assert cfg.sandbox_mode is SandboxMode.READ_ONLY

    def test_list_replaced_not_appended(self, home, project):
        (home / "config.toml").write_text(
            '[sandbox_workspace_write]\nwritable_roots = ["/user-root"]\n'
        )
        _write_project(
            project, {"sandbox_workspace_write": {"writable_roots": ["/proj-root"]}}
        )
        cfg = load_executor_config(project, project_trusted=True, executor_home=home)
        assert cfg.sandbox_workspace_write.writable_roots == ["/proj-root"]

    def test_table_deep_merge_preserves_unmentioned_keys(self, home, project):
        (home / "config.toml").write_text(
            "[sandbox_workspace_write]\nnetwork_access = true\n"
            'writable_roots = ["/data"]\n'
        )
        _write_project(
            project, {"sandbox_workspace_write": {"exclude_slash_tmp": True}}
        )
        cfg = load_executor_config(project, project_trusted=True, executor_home=home)
        knobs = cfg.sandbox_workspace_write
        assert knobs.network_access is True  # user 层保留
        assert knobs.exclude_slash_tmp is True  # project 层补充
        assert knobs.writable_roots == ["/data"]  # 未提及键不受 project 段影响


class TestUnknownKeys:
    def test_top_level_unknown_key_warns_and_ignored(self, home, caplog):
        (home / "config.toml").write_text(
            'sandbox_mode = "read-only"\nfuture_knob = 1\n'
        )
        with caplog.at_level(logging.WARNING):
            cfg = load_executor_config(executor_home=home)
        assert cfg.sandbox_mode is SandboxMode.READ_ONLY
        assert any("future_knob" in r.message for r in caplog.records)

    def test_nested_unknown_key_warns(self, home, caplog):
        (home / "config.toml").write_text(
            'sandbox_mode = "workspace-write"\n'
            "[sandbox_workspace_write]\n"
            'writable_rooots = ["/tmp"]\n'  # 拼错的键（writable_roots）
        )
        with caplog.at_level(logging.WARNING):
            cfg = load_executor_config(executor_home=home)
        # 拼错的键被忽略（不进入物化），并有警告
        assert cfg.sandbox_workspace_write.writable_roots == []
        assert any("writable_rooots" in r.message for r in caplog.records)


class TestValidation:
    def test_bad_enum_value_raises(self, home):
        (home / "config.toml").write_text('sandbox_mode = "yolo"\n')
        with pytest.raises(ConfigError):
            load_executor_config(executor_home=home)

    def test_network_proxy_section(self, home):
        (home / "config.toml").write_text(
            "[network_proxy]\nenabled = true\n"
            'allowed_domains = ["*.example.com"]\n'
            'denied_domains = ["evil.example.com"]\n'
        )
        cfg = load_executor_config(executor_home=home)
        assert cfg.network_proxy is not None
        assert cfg.network_proxy.enabled is True
        assert cfg.network_proxy.mode is NetworkMode.PROXY  # 缺省 proxy
        assert cfg.network_proxy.allowed_domains == ["*.example.com"]
        assert cfg.network_proxy.denied_domains == ["evil.example.com"]
