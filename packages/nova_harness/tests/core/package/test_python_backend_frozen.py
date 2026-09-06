"""冻结形态 Python 安装后端（FrozenSiteBackend）与宿主探测测试。"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nova_harness.core.package.install.python_backend import (
    FrozenSiteBackend,
    NoPipHostError,
    find_host_python,
    get_backend,
)


@pytest.fixture()
def frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)


class TestFindHostPython:
    def test_env_python_wins(self, monkeypatch):
        monkeypatch.setenv("NOVA_PYTHON", "/custom/python")
        with patch("subprocess.run") as run:
            run.return_value = MagicMock(stdout="3.12\n")
            assert find_host_python() == "/custom/python"

    def test_version_mismatch_rejected(self, monkeypatch):
        monkeypatch.setenv("NOVA_PYTHON", "/custom/python")
        monkeypatch.setattr("shutil.which", lambda _name: None)
        with patch("subprocess.run") as run:
            # 版本不一致（当前运行时必为 3.12，伪造成 3.11）
            run.return_value = MagicMock(stdout="3.11\n")
            assert find_host_python() is None

    def test_no_host_returns_none(self, monkeypatch):
        monkeypatch.delenv("NOVA_PYTHON", raising=False)
        monkeypatch.setattr("shutil.which", lambda _name: None)
        assert find_host_python() is None

    def test_pip_missing_rejected(self, monkeypatch):
        monkeypatch.setenv("NOVA_PYTHON", "/custom/python")
        monkeypatch.setattr("shutil.which", lambda _name: None)
        import subprocess as sp

        def _fake_run(cmd, **kwargs):
            if "-m" in cmd and "pip" in cmd:
                raise sp.CalledProcessError(1, cmd)
            return MagicMock(stdout="3.12\n")

        with patch("subprocess.run", side_effect=_fake_run):
            assert find_host_python() is None


class TestFrozenSiteBackend:
    def test_editable_self_install_skipped(self, tmp_path):
        """包自安装（pip -e）在冻结形态零动作（sys.path 挂载替代）。"""
        backend = FrozenSiteBackend(tmp_path / ".site")
        with patch("subprocess.run") as run:
            assert backend.install(["/some/pkg"], editable=True) == ""
            run.assert_not_called()

    def test_install_targets_site_dir(self, tmp_path):
        backend = FrozenSiteBackend(tmp_path / ".site")
        with patch(
            "nova_harness.core.package.install.python_backend.find_host_python",
            return_value="/host/python",
        ):
            with patch("subprocess.run") as run:
                backend.install(["pretty-ms"], requirements_path=None)
        cmd = run.call_args[0][0]
        assert cmd[:3] == ["/host/python", "-m", "pip"]
        assert "--target" in cmd
        assert str(tmp_path / ".site") in cmd
        assert "pretty-ms" in cmd

    def test_no_host_falls_back_to_wheel_channel(self, tmp_path):
        """无 pip 宿主：免 pip wheel 解包通道接管（不再是硬错误）。"""
        backend = FrozenSiteBackend(tmp_path / ".site")
        with patch(
            "nova_harness.core.package.install.python_backend.find_host_python",
            return_value=None,
        ):
            with patch(
                "nova_harness.core.package.install.python_backend.install_wheels"
            ) as wheels:
                wheels.return_value = ["pretty-ms==1.2.3"]
                assert backend.install(["pretty-ms"]) == ""
        wheels.assert_called_once_with(
            ["pretty-ms"], site_dir=str(tmp_path / ".site"), requirements_path=None
        )

    def test_no_host_dry_run_still_raises(self, tmp_path):
        """dry-run 冲突检查无宿主不可用——wheel 通道无 dry-run 语义。"""
        backend = FrozenSiteBackend(tmp_path / ".site")
        with patch(
            "nova_harness.core.package.install.python_backend.find_host_python",
            return_value=None,
        ):
            with pytest.raises(NoPipHostError):
                backend.install(["pretty-ms"], dry_run=True)

    def test_uninstall_removes_dist_dirs(self, tmp_path):
        site = tmp_path / ".site"
        (site / "pretty_ms").mkdir(parents=True)
        (site / "pretty_ms-1.0.0.dist-info").mkdir()
        (site / "other_pkg").mkdir()
        FrozenSiteBackend(site).uninstall("pretty-ms")
        assert not (site / "pretty_ms").exists()
        assert not (site / "pretty_ms-1.0.0.dist-info").exists()
        assert (site / "other_pkg").exists()


def test_get_backend_frozen_dispatch(tmp_path, frozen):
    backend = get_backend(str(tmp_path))
    assert isinstance(backend, FrozenSiteBackend)
    assert backend.site_dir == tmp_path / "packages" / ".site"


def test_get_backend_dev_unchanged(monkeypatch):
    """开发态：uv 优先、pip 兜底（不受冻结分支影响）。"""
    monkeypatch.delattr(sys, "frozen", raising=False)
    with patch(
        "nova_harness.core.package.install.python_backend.find_uv",
        return_value=None,
    ):
        from nova_harness.core.package.install.python_backend import PipBackend

        assert isinstance(get_backend(), PipBackend)
