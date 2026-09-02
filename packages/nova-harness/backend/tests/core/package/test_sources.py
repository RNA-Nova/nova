"""Tests for package source spec parsing and fetching."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from nova_harness.core.package.source.resolver import SourceResolver
from nova_harness.core.package.source.spec import parse_source


def test_parse_path_implicit():
    src = parse_source("./my/pkg")
    assert src.type == "path"
    assert src.spec == "./my/pkg"
    assert src.path == "./my/pkg"


def test_parse_path_explicit():
    src = parse_source("path:/absolute/path")
    assert src.type == "path"
    assert src.spec == "path:/absolute/path"
    assert src.path == "/absolute/path"


def test_parse_git_https_with_ref():
    src = parse_source("https://github.com/user/repo@v1.0.0")
    assert src.type == "git"
    assert src.remote_url == "https://github.com/user/repo"
    assert src.host == "github.com"
    assert src.repo_path == "user/repo"
    assert src.ref == "v1.0.0"


def test_parse_git_colon_with_ref():
    src = parse_source("git:github.com/user/repo@main")
    assert src.type == "git"
    assert src.remote_url == "https://github.com/user/repo"
    assert src.host == "github.com"
    assert src.repo_path == "user/repo"
    assert src.ref == "main"


def test_parse_git_scp():
    src = parse_source("git:git@github.com:user/repo.git@v2")
    assert src.type == "git"
    assert src.remote_url == "git@github.com:user/repo"
    assert src.host == "github.com"
    assert src.repo_path == "user/repo"
    assert src.ref == "v2"


def test_parse_git_commit_ref():
    sha = "abc1234567890abcdef1234567890abcdef1234"
    src = parse_source(f"git:github.com/user/repo@{sha}")
    assert src.ref == sha


def test_resolve_path(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    resolver = SourceResolver(tmp_path / "agent")
    assert resolver.resolve(parse_source(str(pkg))) == str(pkg.resolve())


def test_resolve_path_not_found(tmp_path):
    resolver = SourceResolver(tmp_path / "agent")
    with pytest.raises(ValueError, match="not found"):
        resolver.resolve(parse_source(str(tmp_path / "missing")))


def test_resolve_git_clone_requires_update(tmp_path):
    """clone 只属于安装/更新流程（update=True）。"""
    agent_dir = tmp_path / "agent"
    resolver = SourceResolver(agent_dir)
    src = parse_source("git:github.com/user/repo@main")

    def fake_run(cmd, **kwargs):
        # git clone ... /agent/git/github.com/user/repo.clone-tmp
        if cmd[1] == "clone":
            dest = Path(cmd[-1])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir()
        return subprocess.CompletedProcess(cmd, 0)

    with patch("subprocess.run", side_effect=fake_run):
        resolved = resolver.resolve(src, update=True)

    expected = agent_dir / "packages" / "git" / "github.com" / "user" / "repo"
    assert Path(resolved).resolve() == expected.resolve()
    assert (expected / ".git").exists()


def test_resolve_git_missing_cache_read_only_raises(tmp_path):
    """只读解析（update=False）：git 缓存缺失即"未安装"报错，且不执行任何 git 命令。"""
    agent_dir = tmp_path / "agent"
    resolver = SourceResolver(agent_dir)
    src = parse_source("git:github.com/user/repo@main")

    def fail_run(cmd, **kwargs):
        raise AssertionError(f"read-only resolve must not run git commands: {cmd}")

    with patch("subprocess.run", side_effect=fail_run):
        with pytest.raises(ValueError, match="not installed"):
            resolver.resolve(src)


def test_resolve_git_update(tmp_path):
    """resolve(update=True)：已缓存的 git 源同步到远端最新状态。"""
    agent_dir = tmp_path / "agent"
    cache = agent_dir / "packages" / "git" / "github.com" / "user" / "repo"
    cache.mkdir(parents=True)
    (cache / ".git").mkdir()

    src = parse_source("git:github.com/user/repo@main")
    resolver = SourceResolver(agent_dir)

    calls = []

    def fake_run(cmd, **kwargs):
        # cmd is like ["git", "-C", "<dir>", "fetch", ...]
        subcommand = cmd[3] if cmd[1] == "-C" else cmd[1]
        calls.append(subcommand)
        if subcommand == "remote":
            return subprocess.CompletedProcess(
                cmd, 0, stdout="https://github.com/user/repo\n"
            )
        return subprocess.CompletedProcess(cmd, 0)

    with patch("subprocess.run", side_effect=fake_run):
        resolver.resolve(src, update=True)

    assert "fetch" in calls
    assert "reset" in calls
    assert "checkout" not in calls


def test_resolve_git_cached_is_read_only(tmp_path):
    """默认 resolve：git 缓存已存在时只读返回，不触发任何 git 网络操作。"""
    agent_dir = tmp_path / "agent"
    cache = agent_dir / "packages" / "git" / "github.com" / "user" / "repo"
    cache.mkdir(parents=True)
    (cache / ".git").mkdir()

    src = parse_source("git:github.com/user/repo@main")
    resolver = SourceResolver(agent_dir)

    def fail_run(cmd, **kwargs):
        raise AssertionError(f"resolve must not run git commands, got: {cmd}")

    with patch("subprocess.run", side_effect=fail_run):
        resolved = resolver.resolve(src)

    assert Path(resolved).resolve() == cache.resolve()


def test_resolve_git_update_skips_reset_when_up_to_date(tmp_path):
    """up-to-date 短路：HEAD 已与目标 commit 相同则不执行 reset/clean。"""
    agent_dir = tmp_path / "agent"
    cache = agent_dir / "packages" / "git" / "github.com" / "user" / "repo"
    cache.mkdir(parents=True)
    (cache / ".git").mkdir()

    src = parse_source("git:github.com/user/repo@main")
    resolver = SourceResolver(agent_dir)

    calls = []
    head = "a" * 40

    def fake_run(cmd, **kwargs):
        subcommand = cmd[3] if cmd[1] == "-C" else cmd[1]
        calls.append(subcommand)
        if subcommand == "remote":
            return subprocess.CompletedProcess(
                cmd, 0, stdout="https://github.com/user/repo\n"
            )
        if subcommand == "rev-parse":
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{head}\n")
        return subprocess.CompletedProcess(cmd, 0)

    with patch("subprocess.run", side_effect=fake_run):
        resolver.resolve(src, update=True)

    assert "fetch" in calls
    assert "reset" not in calls
    assert "clean" not in calls
    assert "checkout" not in calls


def test_get_package_identity_git_ignores_ref():
    from nova_harness.core.package.source.spec import get_package_identity

    main = "git:github.com/user/repo@main"
    tag = "git:github.com/user/repo@v1.0"
    sha = "git:github.com/user/repo@abc1234"
    assert (
        get_package_identity(main)
        == get_package_identity(tag)
        == get_package_identity(sha)
    )
    assert get_package_identity(main) == "git:github.com/user/repo"


def test_get_package_identity_path_resolves():
    from nova_harness.core.package.source.spec import get_package_identity

    identity = get_package_identity("path:./my-agent")
    assert identity.startswith("local:")
    assert "/my-agent" in identity


def test_parse_source_rejects_editable_prefix():
    from nova_harness.core.package.source.spec import parse_source

    with pytest.raises(ValueError, match="editable:"):
        parse_source("editable:/path/to/pkg")


def test_get_package_identity_path_resolves():
    from nova_harness.core.package.source.spec import get_package_identity

    identity = get_package_identity("path:./my-agent")
    assert identity.startswith("local:")
    assert "/my-agent" in identity


def test_get_package_identity_editable_dict_same_as_path():
    from nova_harness.core.package.source.spec import get_package_identity

    assert get_package_identity("path:./my-agent") == get_package_identity(
        {"source": "path:./my-agent", "editable": True}
    )


def test_get_package_identity_path_and_editable_same():
    from nova_harness.core.package.source.spec import get_package_identity

    assert get_package_identity("path:./my-agent") == get_package_identity(
        {"source": "path:./my-agent", "editable": True}
    )


def test_install_path_for_source_rejects_escape():
    from nova_harness.core.package.install.store import install_path_for_source
    from nova_harness.core.package.source.spec import parse_source

    root = Path("/tmp/nova/packages")
    git_root = root / "git"
    path_root = root / "path"

    src = parse_source("git:github.com/../../evil/repo")
    with pytest.raises(ValueError, match="outside package install root"):
        install_path_for_source(src, "", path_root, git_root)


def test_resolve_managed_path_rejects_escape(tmp_path):
    from nova_harness.core.package.install.store import resolve_managed_path

    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValueError, match="outside package install root"):
        resolve_managed_path(root, "..", "outside")


def test_resolve_managed_path_allows_nested(tmp_path):
    from nova_harness.core.package.install.store import resolve_managed_path

    root = tmp_path / "root"
    root.mkdir()
    result = resolve_managed_path(root, "a", "b")
    assert result == (root / "a" / "b").resolve()
