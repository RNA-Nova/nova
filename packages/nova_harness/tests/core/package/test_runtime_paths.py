"""冻结形态运行时装配路径挂载（runtime_paths.ensure_package_paths）测试。"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

from nova_harness.core.package.runtime_paths import ensure_package_paths


def _frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)


def _clean_sys_path(monkeypatch):
    """隔离 sys.path 现场。"""
    snapshot = list(sys.path)
    monkeypatch.setattr(sys, "path", snapshot)


def test_not_frozen_noop(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    sm = MagicMock()
    sm.get_package_sources.return_value = []
    assert ensure_package_paths(str(tmp_path), sm) == []


def test_mounts_site_and_store_backends(tmp_path, monkeypatch):
    """.site + 存储族（path/git/npm）下各包 backend/ 全部挂载。"""
    _frozen(monkeypatch)
    _clean_sys_path(monkeypatch)
    agent = tmp_path / "agent"
    site = agent / "packages" / ".site"
    site.mkdir(parents=True)
    (agent / "packages" / "path" / "pkg-a" / "backend").mkdir(parents=True)
    (agent / "packages" / "git" / "pkg-b" / "backend").mkdir(parents=True)
    # 无 backend 的包不挂
    (agent / "packages" / "npm" / "pkg-c").mkdir(parents=True)

    sm = MagicMock()
    sm.get_package_sources.return_value = []

    mounted = ensure_package_paths(str(agent), sm)
    assert str(site) in mounted
    assert str(agent / "packages" / "path" / "pkg-a" / "backend") in mounted
    assert str(agent / "packages" / "git" / "pkg-b" / "backend") in mounted
    assert not any("pkg-c" in m for m in mounted)
    # 真实挂进 sys.path（append——冻结内部优先，包不能遮蔽 nova_*）
    assert str(site) in sys.path
    assert sys.path.index(str(site)) > 0 or len(sys.path) == 1


def test_mounts_editable_path_source_backend(tmp_path, monkeypatch):
    """editable path 源（原地引用不进存储目录）的 backend/ 也挂载。"""
    _frozen(monkeypatch)
    _clean_sys_path(monkeypatch)
    agent = tmp_path / "agent"
    (agent / "packages").mkdir(parents=True)
    external = tmp_path / "external-pkg"
    (external / "backend").mkdir(parents=True)

    sm = MagicMock()
    sm.get_package_sources.return_value = [f"path:{external}"]

    mounted = ensure_package_paths(str(agent), sm)
    assert str(external / "backend") in mounted


def test_idempotent_no_duplicates(tmp_path, monkeypatch):
    _frozen(monkeypatch)
    _clean_sys_path(monkeypatch)
    agent = tmp_path / "agent"
    site = agent / "packages" / ".site"
    site.mkdir(parents=True)
    sm = MagicMock()
    sm.get_package_sources.return_value = []

    first = ensure_package_paths(str(agent), sm)
    second = ensure_package_paths(str(agent), sm)
    assert first == [str(site)]
    assert second == []


def test_project_scope_site_also_mounted(tmp_path, monkeypatch):
    """项目级 .site（<cwd>/.nova/packages/.site）同挂。"""
    _frozen(monkeypatch)
    _clean_sys_path(monkeypatch)
    agent = tmp_path / "agent"
    (agent / "packages").mkdir(parents=True)
    project = tmp_path / "proj" / ".nova"
    (project / "packages" / ".site").mkdir(parents=True)

    sm = MagicMock()
    sm.get_package_sources.return_value = []
    mounted = ensure_package_paths(str(agent), sm, project_base_dir=str(project))
    assert str(project / "packages" / ".site") in mounted
