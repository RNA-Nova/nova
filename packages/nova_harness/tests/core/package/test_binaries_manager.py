"""core/package/binaries/manager.py 单元测试。

下载路径用 ``file://`` URL + 本地构造的 tar.gz/zip 资产模拟，不依赖网络。
"""

import hashlib
import io
import json
import os
import stat
import sys
import tarfile
from pathlib import Path
import zipfile

import pytest
from nova_harness.core.package.binaries import manager
from nova_harness.core.package.binaries.manager import (
    detect_platform_key,
    ensure_binary,
    is_offline_mode_enabled,
)


def _exe(name: str) -> str:
    """平台可执行文件名（Windows 为 <name>.exe）。"""
    return name + (".exe" if sys.platform == "win32" else "")


def _make_executable_bytes() -> bytes:
    return b"#!/bin/sh\necho fake-binary\n"


def _make_tar_gz(tmp_path, binary_name: str) -> str:
    archive = tmp_path / f"{binary_name}-asset.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        data = _make_executable_bytes()
        info = tarfile.TarInfo(name=f"pkg/{_exe(binary_name)}")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return str(archive)


def _make_zip(tmp_path, binary_name: str) -> str:
    archive = tmp_path / f"{binary_name}-asset.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"pkg/{_exe(binary_name)}", _make_executable_bytes())
    return str(archive)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        digest.update(f.read())
    return digest.hexdigest()


def _fake_registry(monkeypatch, name: str, url: str, sha256: str) -> None:
    registry = {
        name: {
            "version": "1.0.0",
            "binary_name": name,
            "platforms": {
                detect_platform_key(): {"url": url, "sha256": sha256},
            },
        }
    }
    monkeypatch.setattr(manager, "_load_registry", lambda: registry)


def test_detect_platform_key():
    key = detect_platform_key()
    assert key is not None
    platform_part, arch = key.rsplit("-", 1)
    assert platform_part in ("darwin", "linux", "windows")
    assert arch in ("x86_64", "aarch64")


def test_offline_mode(monkeypatch):
    monkeypatch.setenv("NOVA_OFFLINE", "1")
    assert is_offline_mode_enabled() is True
    monkeypatch.setenv("NOVA_OFFLINE", "true")
    assert is_offline_mode_enabled() is True
    monkeypatch.setenv("NOVA_OFFLINE", "0")
    assert is_offline_mode_enabled() is False
    monkeypatch.delenv("NOVA_OFFLINE")
    assert is_offline_mode_enabled() is False


def test_ensure_binary_already_installed(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    target = bin_dir / _exe("fd")
    target.touch()
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    assert ensure_binary("fd", bin_dir=str(bin_dir)) == str(target)


def test_ensure_binary_unknown_name(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "_load_registry", lambda: {})
    assert ensure_binary("no-such-tool", bin_dir=str(tmp_path)) is None


def test_ensure_binary_offline_skips_download(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVA_OFFLINE", "1")
    _fake_registry(monkeypatch, "fd", "file:///nonexistent", "0" * 64)
    assert ensure_binary("fd", bin_dir=str(tmp_path)) is None


def test_ensure_binary_no_platform_entry(tmp_path, monkeypatch):
    registry = {"fd": {"version": "1.0.0", "binary_name": "fd", "platforms": {}}}
    monkeypatch.setattr(manager, "_load_registry", lambda: registry)
    assert ensure_binary("fd", bin_dir=str(tmp_path)) is None


def test_ensure_binary_download_install_tar_gz(tmp_path, monkeypatch):
    archive = _make_tar_gz(tmp_path, "fd")
    _fake_registry(monkeypatch, "fd", Path(archive).as_uri(), _sha256(archive))
    bin_dir = tmp_path / "bin"

    progress = []
    result = ensure_binary("fd", bin_dir=str(bin_dir), on_progress=progress.append)
    assert result == str(bin_dir / _exe("fd"))
    assert os.access(result, os.X_OK)
    assert open(result, "rb").read() == _make_executable_bytes()
    assert progress and "Downloading fd 1.0.0" in progress[0]
    # staging 目录已清理
    assert not [p for p in bin_dir.iterdir() if p.name.startswith(".install-")]


def test_ensure_binary_download_install_zip(tmp_path, monkeypatch):
    archive = _make_zip(tmp_path, "fd")
    _fake_registry(monkeypatch, "fd", Path(archive).as_uri(), _sha256(archive))
    bin_dir = tmp_path / "bin"
    result = ensure_binary("fd", bin_dir=str(bin_dir))
    assert result == str(bin_dir / _exe("fd"))


def test_ensure_binary_checksum_mismatch(tmp_path, monkeypatch):
    archive = _make_tar_gz(tmp_path, "fd")
    _fake_registry(monkeypatch, "fd", f"file://{archive}", "0" * 64)
    bin_dir = tmp_path / "bin"
    assert ensure_binary("fd", bin_dir=str(bin_dir)) is None
    # 校验失败不留任何文件
    assert not (bin_dir / _exe("fd")).exists()


def test_ensure_binary_missing_binary_in_archive(tmp_path, monkeypatch):
    archive = _make_tar_gz(tmp_path, "other-name")
    _fake_registry(monkeypatch, "fd", Path(archive).as_uri(), _sha256(archive))
    assert ensure_binary("fd", bin_dir=str(tmp_path / "bin")) is None


def test_registry_file_loads():
    """真实 registry.json：注册表只收"PyPI 覆盖不了"的二进制（一 binary 一家）。"""
    registry = manager._load_registry()
    assert set(registry) == {"fd"}
    for name, entry in registry.items():
        assert entry["version"]
        assert entry["binary_name"] == name
        assert entry["platforms"], f"{name} 应至少有一个平台条目"
        for key, asset in entry["platforms"].items():
            normalized = key.removesuffix("-musl")
            platform_part, arch = normalized.rsplit("-", 1)
            assert platform_part in ("darwin", "linux", "windows")
            assert arch in ("x86_64", "aarch64")
            assert asset["url"].startswith("https://")
            assert len(asset["sha256"]) == 64


@pytest.mark.integration
def test_ensure_binary_real_download_fd(tmp_path):
    """真实下载 fd（当前平台）并验证可执行。"""
    import subprocess

    result = ensure_binary("fd", bin_dir=str(tmp_path))
    assert result is not None
    out = subprocess.run([result, "--version"], capture_output=True, text=True)
    assert out.returncode == 0
    assert out.stdout.strip().startswith("fd ")
