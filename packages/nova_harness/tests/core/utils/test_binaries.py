"""core/utils/binaries.py 单元测试。"""

import stat
import sys
from pathlib import Path

import pytest

from nova_harness.core.utils import binaries
from nova_harness.core.utils.binaries import (
    get_env_bin_dir,
    get_nova_bin_dir,
    prepend_managed_bins_to_path,
    resolve_binary,
)


@pytest.fixture
def fake_bins(tmp_path, monkeypatch):
    """构造可控的 env bin 与 nova bin。"""
    env_bin = tmp_path / "env_bin"
    env_bin.mkdir()
    (env_bin / "python").touch()
    monkeypatch.setattr(sys, "executable", str(env_bin / "python"))

    nova_bin = tmp_path / "nova_bin"
    nova_bin.mkdir()
    monkeypatch.setattr(binaries, "get_nova_bin_dir", lambda: str(nova_bin))
    return {"env_bin": env_bin, "nova_bin": nova_bin}


def _make_executable(path) -> None:
    path.touch()
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_resolve_binary_prefers_env_bin(fake_bins):
    _make_executable(fake_bins["env_bin"] / "rg")
    assert resolve_binary("rg") == str(fake_bins["env_bin"] / "rg")


def test_resolve_binary_nova_bin_tier(fake_bins, monkeypatch):
    """env bin 没有 → nova bin（注册表自管理）命中。"""
    monkeypatch.setenv("PATH", "/nonexistent-xyz")
    _make_executable(fake_bins["nova_bin"] / "fd")
    assert resolve_binary("fd") == str(fake_bins["nova_bin"] / "fd")


def test_resolve_binary_falls_back_to_path(fake_bins, tmp_path, monkeypatch):
    system_bin = tmp_path / "sysbin"
    system_bin.mkdir()
    _make_executable(system_bin / "fd")
    monkeypatch.setenv("PATH", str(system_bin))
    assert resolve_binary("fd") == str(system_bin / "fd")


def test_resolve_binary_priority_order(fake_bins, tmp_path, monkeypatch):
    """env bin > nova bin > PATH。"""
    _make_executable(fake_bins["env_bin"] / "rg")
    _make_executable(fake_bins["nova_bin"] / "rg")
    system_bin = tmp_path / "sysbin"
    system_bin.mkdir()
    _make_executable(system_bin / "rg")
    monkeypatch.setenv("PATH", str(system_bin))
    assert resolve_binary("rg") == str(fake_bins["env_bin"] / "rg")


def test_resolve_binary_missing_returns_none(fake_bins, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent-xyz")
    assert resolve_binary("no-such-binary-xyz") is None


def test_resolve_binary_requires_executable_bit(fake_bins, monkeypatch):
    (fake_bins["env_bin"] / "rg").touch()
    (fake_bins["nova_bin"] / "rg").touch()
    monkeypatch.setenv("PATH", "/nonexistent-xyz")
    assert resolve_binary("rg") is None


def test_prepend_managed_bins(fake_bins, monkeypatch):
    env = prepend_managed_bins_to_path({"PATH": "/usr/bin:/bin"})
    # 顺序与 resolve_binary 优先级一致：env bin → nova bin
    expected = f"{fake_bins['env_bin']}:{fake_bins['nova_bin']}:/usr/bin:/bin"
    assert env["PATH"] == expected


def test_prepend_managed_bins_no_duplicate(fake_bins):
    env = prepend_managed_bins_to_path(
        {"PATH": f"{fake_bins['nova_bin']}:{fake_bins['env_bin']}:/usr/bin"}
    )
    assert env["PATH"] == f"{fake_bins['nova_bin']}:{fake_bins['env_bin']}:/usr/bin"


def test_get_env_bin_dir(fake_bins):
    assert get_env_bin_dir() == str(fake_bins["env_bin"])


def test_get_nova_bin_dir_respects_agent_dir_env(monkeypatch, tmp_path):
    """nova bin 落在 agent 配置目录下（尊重 NOVA_AGENT_DIR 覆盖）。"""
    monkeypatch.setenv("NOVA_AGENT_DIR", str(tmp_path / "agent"))
    assert get_nova_bin_dir() == str(tmp_path / "agent" / "bin")


def test_resolve_binary_alternate_system_names(fake_bins, tmp_path, monkeypatch):
    """PATH 层识别发行版别名（Debian 的 fd 叫 fdfind）。"""
    system_bin = tmp_path / "sysbin"
    system_bin.mkdir()
    _make_executable(system_bin / "fdfind")
    monkeypatch.setenv("PATH", str(system_bin))
    assert resolve_binary("fd") == str(system_bin / "fdfind")


def test_alternate_names_not_used_in_managed_dirs(fake_bins, monkeypatch):
    """托管目录只用规范名（fdfind 放在 nova bin 不算数）。"""
    _make_executable(fake_bins["nova_bin"] / "fdfind")
    monkeypatch.setenv("PATH", "/nonexistent-xyz")
    assert resolve_binary("fd") is None


def test_binary_install_guidance():
    from nova_harness.core.utils.binaries import binary_install_guidance

    assert "brew install fd" in binary_install_guidance("fd")
    assert "fd-find" in binary_install_guidance("fd")
    assert "brew install ripgrep" in binary_install_guidance("rg")
    assert "官方安装文档" in binary_install_guidance("unknown-tool")
