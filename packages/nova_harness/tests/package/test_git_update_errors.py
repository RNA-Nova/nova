"""git 源更新错误语义：在线失败硬报错，离线才回退本地 ref。"""

import subprocess
from pathlib import Path

import pytest

from nova_harness.core.types.package import PackageSource
from nova_harness.package.source.resolver import SourceResolver


def _git_source(ref: str = "main") -> PackageSource:
    spec = "git:github.com/a/b" + (f"@{ref}" if ref else "")
    return PackageSource(
        type="git",
        spec=spec,
        remote_url="https://github.com/a/b",
        host="github.com",
        repo_path="a/b",
        ref=ref,
    )


def _make_cache(tmp_path: Path) -> Path:
    cache = tmp_path / "packages" / "git" / "github.com" / "a" / "b"
    (cache / ".git").mkdir(parents=True)
    return cache


def _force_online(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "nova_harness.package.source.resolver.is_offline_mode_enabled",
        lambda: False,
    )


def test_online_explicit_ref_fetch_failure_is_hard_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """在线 fetch 失败必须抛错——不能静默回退本地陈旧 ref 还报告 Updated。"""
    resolver = SourceResolver(tmp_path)
    cache = _make_cache(tmp_path)
    _force_online(monkeypatch)

    def fake_git_run(args, **kwargs):
        if "fetch" in args:
            raise subprocess.CalledProcessError(128, args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(resolver, "_git_run", fake_git_run)

    with pytest.raises(subprocess.CalledProcessError):
        resolver._git_update(cache, _git_source(ref="main"))


def test_online_branch_fetch_failure_is_hard_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无显式 ref 时在线 fetch 失败同样是硬错误（对齐 TS runCommand 抛错）。"""
    resolver = SourceResolver(tmp_path)
    cache = _make_cache(tmp_path)
    _force_online(monkeypatch)
    monkeypatch.setattr(
        resolver,
        "_git_update_target",
        lambda c: {
            "ref": "@{upstream}",
            "fetch_refspec": ["+refs/heads/main:refs/remotes/origin/main"],
        },
    )

    def fake_git_run(args, **kwargs):
        if "fetch" in args:
            raise subprocess.CalledProcessError(128, args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(resolver, "_git_run", fake_git_run)

    with pytest.raises(subprocess.CalledProcessError):
        resolver._git_update(cache, _git_source(ref=None))


def test_offline_falls_back_to_local_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """离线模式回退本地 ref：HEAD 已等于目标时报告 up-to-date，且不触网。"""
    resolver = SourceResolver(tmp_path)
    cache = _make_cache(tmp_path)
    monkeypatch.setattr(
        "nova_harness.package.source.resolver.is_offline_mode_enabled",
        lambda: True,
    )
    calls = []

    def fake_git_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="abc123\n", stderr="")

    monkeypatch.setattr(resolver, "_git_run", fake_git_run)
    monkeypatch.setattr(resolver, "_git_head_matches", lambda c, r: True)

    resolver._git_update(cache, _git_source(ref="main"))

    assert not any("fetch" in args for args in calls)
