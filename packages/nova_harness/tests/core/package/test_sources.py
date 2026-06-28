"""Tests for package_manager/sources.py."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from nova_harness.core.package.sources import (
    SourceResolver,
    parse_source,
)


def test_parse_local_implicit():
    src = parse_source("./my/pkg")
    assert src.type == "local"
    assert src.spec == "./my/pkg"
    assert src.path == "./my/pkg"


def test_parse_local_explicit():
    src = parse_source("local:/absolute/path")
    assert src.type == "local"
    assert src.spec == "local:/absolute/path"
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


def test_resolve_local(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    resolver = SourceResolver(tmp_path / "agent")
    assert resolver.resolve(parse_source(str(pkg))) == str(pkg.resolve())


def test_resolve_local_not_found(tmp_path):
    resolver = SourceResolver(tmp_path / "agent")
    with pytest.raises(ValueError, match="not found"):
        resolver.resolve(parse_source(str(tmp_path / "missing")))


def test_resolve_git_clone(tmp_path):
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
        resolved = resolver.resolve(src)

    expected = agent_dir / "packages" / "git" / "github.com" / "user" / "repo"
    assert Path(resolved).resolve() == expected.resolve()
    assert (expected / ".git").exists()


def test_resolve_git_update(tmp_path):
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
        return subprocess.CompletedProcess(cmd, 0)

    with patch("subprocess.run", side_effect=fake_run):
        resolver.resolve(src)

    assert "fetch" in calls
    assert "checkout" in calls
