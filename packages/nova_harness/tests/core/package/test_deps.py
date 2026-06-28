"""
包依赖安装单元测试。
"""

import subprocess
import sys
from unittest.mock import patch

import pytest

from nova_harness.core.package.deps import find_uv, install_dependencies


def test_find_uv_found():
    with patch("shutil.which", return_value="/usr/bin/uv"):
        assert find_uv() == "/usr/bin/uv"


def test_find_uv_not_found():
    with patch("shutil.which", return_value=None):
        assert find_uv() is None


def test_install_dependencies_no_work():
    """没有依赖和 requirements 时不调用 subprocess。"""
    with patch("subprocess.run") as mock_run:
        install_dependencies([], requirements_path=None)
        mock_run.assert_not_called()


def test_install_dependencies_with_uv():
    """找到 uv 时使用 uv pip install。"""
    with patch("shutil.which", return_value="/usr/bin/uv"):
        with patch("subprocess.run") as mock_run:
            install_dependencies(
                ["requests>=2.0"], python_executable="/usr/bin/python3"
            )
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "/usr/bin/uv"
            assert "pip" in args
            assert "--python" in args
            assert "/usr/bin/python3" in args
            assert "requests>=2.0" in args


def test_install_dependencies_without_uv():
    """未找到 uv 时回退到 python -m pip install。"""
    with patch("shutil.which", return_value=None):
        with patch("subprocess.run") as mock_run:
            install_dependencies(["requests>=2.0"])
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == sys.executable
            assert "-m" in args
            assert "pip" in args
            assert "install" in args
            assert "requests>=2.0" in args


@pytest.mark.parametrize("uv_available", [True, False])
def test_install_dependencies_with_requirements(uv_available):
    """带 requirements.txt 时传递 --requirements/--requirement 参数。"""
    with patch("shutil.which", return_value="/usr/bin/uv" if uv_available else None):
        with patch("subprocess.run") as mock_run:
            install_dependencies(["requests>=2.0"], requirements_path="/tmp/req.txt")
            args = mock_run.call_args[0][0]
            if uv_available:
                assert "--requirements" in args
                assert "/tmp/req.txt" in args
            else:
                assert "--requirement" in args
                assert "/tmp/req.txt" in args


def test_install_dependencies_called_process_error():
    """subprocess 失败时抛出 CalledProcessError。"""
    with patch("shutil.which", return_value=None):
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["pip", "install"]),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                install_dependencies(["missing-pkg"])


def test_install_dependencies_quiet_flag():
    """quiet=True 时包含 --quiet。"""
    with patch("shutil.which", return_value=None):
        with patch("subprocess.run") as mock_run:
            install_dependencies(["requests>=2.0"], quiet=True)
            args = mock_run.call_args[0][0]
            assert "--quiet" in args
