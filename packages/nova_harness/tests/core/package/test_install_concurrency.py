"""安装并发与 npm 本地可解析判定回归（子代理并行委派互踩事故）。

事故画像（Windows 实机）：两个 worker 子代理 CLI 同时启动，npm 源恒判
"不可解析"→ 双双全量重装同一包，一个把另一个 resolve 到一半的目录
删换——"Path escapes package root" 竞态炸点。修复 = 本地判定（装了
且版本满足即不重装）+ 跨进程安装锁 + 锁内复查。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from nova_harness.core.package import PackageManager
from nova_harness.core.package.install.installer import PackageInstaller
from nova_harness.core.package.manager import SourceScope


@pytest.fixture()
def pm(tmp_path):
    return PackageManager(
        agent_dir=str(tmp_path / "agent"),
        cwd=str(tmp_path),
        project_trusted=True,
    )


@pytest.fixture(autouse=True)
def no_dependency_install():
    """避免测试执行真实 pip/uv 命令。"""
    with patch("nova_harness.core.package.install.installer.install_dependencies"):
        with patch("nova_harness.core.package.install.installer.install_package"):
            with patch("nova_harness.core.package.install.installer.uninstall_package"):
                with patch("nova_harness.core.package.manager.uninstall_package"):
                    yield


def _plant_npm_cache(pm: PackageManager, name: str, version: str) -> Path:
    """在 user scope 的 npm 缓存目录（即安装态）写入一个已装包。"""
    pkg_dir = (
        pm._user_installer.npm_root
        / pm._user_installer.source_resolver.npm_safe_name(name)
    )
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "package.json").write_text(
        json.dumps({"name": name, "version": version}), encoding="utf-8"
    )
    return pkg_dir


class TestNpmLocalResolvability:
    """npm 源本地可解析判定（不触网）。"""

    def test_installed_no_spec_is_resolvable(self, pm):
        _plant_npm_cache(pm, "demo-pkg", "1.2.0")
        assert pm._is_package_resolvable("npm:demo-pkg", SourceScope.USER) is True

    def test_missing_cache_is_not_resolvable(self, pm):
        assert pm._is_package_resolvable("npm:demo-pkg", SourceScope.USER) is False

    def test_exact_version_match(self, pm):
        _plant_npm_cache(pm, "demo-pkg", "1.2.0")
        assert pm._is_package_resolvable("npm:demo-pkg@1.2.0", SourceScope.USER) is True
        assert (
            pm._is_package_resolvable("npm:demo-pkg@1.3.0", SourceScope.USER) is False
        )

    def test_range_spec(self, pm):
        _plant_npm_cache(pm, "demo-pkg", "1.2.0")
        assert (
            pm._is_package_resolvable("npm:demo-pkg@^1.0.0", SourceScope.USER) is True
        )
        assert (
            pm._is_package_resolvable("npm:demo-pkg@^2.0.0", SourceScope.USER) is False
        )

    def test_dist_tag_satisfied_when_installed(self, pm):
        """dist-tag（beta 等）本地不可判定——装了即视为满足（滚动更新归
        nova-pkg update 显式触发）。"""
        _plant_npm_cache(pm, "demo-pkg", "1.2.0-beta.1")
        assert pm._is_package_resolvable("npm:demo-pkg@beta", SourceScope.USER) is True

    def test_corrupt_package_json_triggers_reinstall(self, pm):
        pkg_dir = _plant_npm_cache(pm, "demo-pkg", "1.2.0")
        (pkg_dir / "package.json").write_text("not json", encoding="utf-8")
        assert (
            pm._is_package_resolvable("npm:demo-pkg@^1.0.0", SourceScope.USER) is False
        )


class TestInstallLock:
    """跨进程安装锁（同进程多线程验证串行化——OS 文件锁跨进程由 filelock 保证）。"""

    def test_concurrent_installs_are_serialized(self, tmp_path):
        installer = PackageInstaller(
            agent_dir=str(tmp_path / "agent"), cwd=str(tmp_path)
        )
        # 最小合法包（一个工具文件）
        pkg_src = tmp_path / "pkg-src"
        (pkg_src / "tools").mkdir(parents=True)
        (pkg_src / "pyproject.toml").write_text(
            '[tool.poetry]\nname = "pkg-x"\nversion = "1.0.0"\n'
            '[tool.nova]\ntools = ["./tools/t.py"]\n',
            encoding="utf-8",
        )
        (pkg_src / "tools" / "t.py").write_text('"""fixture"""\n', encoding="utf-8")

        markers: list[str] = []
        real_resolve = installer.source_resolver.resolve

        def _slow_resolve(source_obj, **kwargs):
            markers.append("in")
            time.sleep(0.2)  # 模拟 npm 下载/装配窗口
            markers.append("out")
            return real_resolve(source_obj, **kwargs)

        errors: list[BaseException] = []

        def _worker():
            try:
                installer.install(f"path:{pkg_src}", quiet=True)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        with patch.object(
            installer.source_resolver, "resolve", side_effect=_slow_resolve
        ):
            threads = [threading.Thread(target=_worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

        assert not errors, errors
        # 串行化证据：in/out 严格成对嵌套或顺序，绝不交错（in,in,...,out,out）
        assert markers == ["in", "out", "in", "out"]


class TestEnsureRechecksInsideLock:
    """锁内复查：并发 ensure 同时判"未装"，实际只装一次。"""

    def test_concurrent_ensure_installs_once(self, pm, monkeypatch):
        install_calls: list[str] = []

        def _spy_install(self, source, **kwargs):
            install_calls.append(str(source))
            # 模拟真实安装：装完缓存就位（复查方能判"已装"）。
            # 返回值 ensure 路径不消费，从简。
            _plant_npm_cache(pm, "demo-pkg", "1.2.0")
            return None

        monkeypatch.setattr(PackageInstaller, "install", _spy_install)

        async def _run_twice():
            await asyncio.gather(
                pm._ensure_packages_installed(["npm:demo-pkg"], SourceScope.USER),
                pm._ensure_packages_installed(["npm:demo-pkg"], SourceScope.USER),
            )

        asyncio.run(_run_twice())
        assert len(install_calls) == 1
