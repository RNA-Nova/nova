"""测试可用更新检查。"""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from nova_harness.core.package.install.updates import (
    _git_local_head,
    _git_remote_head,
    _is_pinned_git_ref,
    check_for_available_updates,
)
from nova_harness.core.types.package import PackageUpdate, SourceScope


@pytest.mark.parametrize(
    "ref,expected",
    [
        ("abc123", False),
        ("main", False),
        ("v1.0.0", False),
        ("a" * 40, True),
        ("ABCD1234" + "0" * 32, True),
        (None, False),
    ],
)
def test_is_pinned_git_ref(ref, expected):
    assert _is_pinned_git_ref(ref) is expected


def test_git_local_head_reads_head(tmp_path):
    """_git_local_head 应能读取初始化后的 git 仓库 HEAD。"""
    import subprocess

    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / "file.txt").write_text("hello")
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "a@b.c"], check=True
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "--quiet", "-m", "init"], check=True
    )

    head = _git_local_head(tmp_path)
    assert head is not None
    assert len(head) == 40


def test_git_remote_head_no_repo(tmp_path):
    """对非 git 目录应返回 None。"""
    assert _git_remote_head(tmp_path, None) is None


def test_check_for_available_updates_skips_path_sources(tmp_path):
    """path 源应被跳过。"""
    scoped = [
        (SourceScope.USER, str(tmp_path), tmp_path, tmp_path),
    ]
    result = asyncio.run(check_for_available_updates(scoped))
    assert result == []


def test_check_for_available_updates_skips_pinned_ref(tmp_path):
    """固定 commit 的 git 源应被跳过。"""
    scoped = [
        (
            SourceScope.USER,
            "git:github.com/user/repo@" + "a" * 40,
            tmp_path,
            tmp_path,
        ),
    ]
    result = asyncio.run(check_for_available_updates(scoped))
    assert result == []


def test_check_for_available_updates_detects_git_update(tmp_path, monkeypatch):
    """本地 HEAD 与远程 HEAD 不一致时应报告可更新。"""
    path_root = tmp_path / "path"
    git_root = tmp_path / "git"
    path_root.mkdir()
    git_root.mkdir()

    source = "git:github.com/user/repo"
    cache_dir = git_root / "github.com" / "user" / "repo"
    cache_dir.mkdir(parents=True)
    (cache_dir / ".git").mkdir()

    local_head = "1" * 40
    remote_head = "2" * 40

    def fake_local_head(d: Path):
        return local_head

    def fake_remote_head(d: Path, ref):
        return remote_head

    monkeypatch.setattr(
        "nova_harness.core.package.install.updates._git_local_head", fake_local_head
    )
    monkeypatch.setattr(
        "nova_harness.core.package.install.updates._git_remote_head", fake_remote_head
    )

    scoped = [(SourceScope.USER, source, path_root, git_root)]
    result = asyncio.run(check_for_available_updates(scoped))

    assert len(result) == 1
    update = result[0]
    assert isinstance(update, PackageUpdate)
    assert update.source == source
    assert update.display_name == "github.com/user/repo"
    assert update.type == "git"
    assert update.scope == SourceScope.USER


def test_check_for_available_updates_no_update(tmp_path, monkeypatch):
    """本地 HEAD 与远程 HEAD 一致时不应报告。"""
    path_root = tmp_path / "path"
    git_root = tmp_path / "git"
    path_root.mkdir()
    git_root.mkdir()

    source = "git:github.com/user/repo"
    cache_dir = git_root / "github.com" / "user" / "repo"
    cache_dir.mkdir(parents=True)
    (cache_dir / ".git").mkdir()

    head = "1" * 40

    monkeypatch.setattr(
        "nova_harness.core.package.install.updates._git_local_head", lambda d: head
    )
    monkeypatch.setattr(
        "nova_harness.core.package.install.updates._git_remote_head",
        lambda d, ref: head,
    )

    scoped = [(SourceScope.USER, source, path_root, git_root)]
    result = asyncio.run(check_for_available_updates(scoped))
    assert result == []


def test_package_manager_check_for_available_updates_uses_settings_manager(
    tmp_path, monkeypatch
):
    """PackageManager.check_for_available_updates 应通过 SettingsManager 读取包源，
    而不是访问已不存在的 _settings_store 属性。"""
    import asyncio

    from nova_harness.core.package import PackageManager

    pm = PackageManager(agent_dir=str(tmp_path / "agent"))

    # 添加一个 path 源到 settings，应被跳过。
    pm.settings_manager.add_package_source(
        str(tmp_path / "some-pkg"),
        local=False,
        base_dir=str(pm._user_installer.install_dir),
        cwd=str(pm.cwd),
    )

    result = asyncio.run(pm.check_for_available_updates())
    assert result == []


def test_check_for_available_updates_respects_offline_mode(monkeypatch):
    """离线模式应返回空列表。"""
    monkeypatch.setenv("NOVA_OFFLINE", "1")
    scoped = [(SourceScope.USER, "git:github.com/user/repo", Path("."), Path("."))]
    result = asyncio.run(check_for_available_updates(scoped))
    assert result == []


def test_check_for_available_updates_swallows_check_errors(tmp_path, monkeypatch):
    """单个源检查失败不应影响其他源。"""
    path_root = tmp_path / "path"
    git_root = tmp_path / "git"
    path_root.mkdir()
    git_root.mkdir()

    source = "git:github.com/user/repo"
    cache_dir = git_root / "github.com" / "user" / "repo"
    cache_dir.mkdir(parents=True)
    (cache_dir / ".git").mkdir()

    monkeypatch.setattr(
        "nova_harness.core.package.install.updates._git_local_head",
        lambda d: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    scoped = [(SourceScope.USER, source, path_root, git_root)]
    result = asyncio.run(check_for_available_updates(scoped))
    assert result == []
