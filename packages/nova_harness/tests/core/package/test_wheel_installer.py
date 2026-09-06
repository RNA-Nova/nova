"""免 pip wheel 解包通道（wheel_installer）测试。

全程零网络：PyPI JSON 由 monkeypatch 伪造，wheel 文件由 zipfile 现造
（经 file:// URL 走真实下载/校验路径）。
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from nova_harness.core.package.install import wheel_installer
from nova_harness.core.package.install.wheel_installer import (
    WheelInstallError,
    install_wheels,
)


def _build_wheel(
    path: Path,
    name: str,
    version: str,
    *,
    tag: str = "py3-none-any",
    requires: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """在 *path* 造一个最小合法 wheel，返回 (filename, sha256)。"""
    module = name.replace("-", "_")
    filename = f"{module}-{version}-{tag}.whl"
    meta = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
    ]
    for req in requires or []:
        meta.append(f"Requires-Dist: {req}")
    wheel_meta = (
        "Wheel-Version: 1.0\nGenerator: nova-test\nRoot-Is-Purelib: true\n"
        f"Tag: {tag}\n"
    )
    with zipfile.ZipFile(path / filename, "w") as zf:
        zf.writestr(f"{module}/__init__.py", f"__version__ = {version!r}\n")
        zf.writestr(f"{module}-{version}.dist-info/METADATA", "\n".join(meta) + "\n")
        zf.writestr(f"{module}-{version}.dist-info/WHEEL", wheel_meta)
    digest = hashlib.sha256((path / filename).read_bytes()).hexdigest()
    return filename, digest


class _FakePypi:
    """按 (name, version) 登记 wheel 文件，产出 PyPI JSON 响应。"""

    def __init__(self) -> None:
        self._releases: Dict[str, Dict[str, List[Tuple[str, Path, str]]]] = {}

    def add(self, name: str, version: str, wheel: Tuple[str, Path, str]) -> None:
        filename, wheel_path, sha256 = wheel
        entry = {
            "filename": filename,
            "packagetype": "bdist_wheel",
            "yanked": False,
            "url": wheel_path.as_uri(),
            "digests": {"sha256": sha256},
        }
        self._releases.setdefault(name, {}).setdefault(version, []).append(entry)

    def json_for(self, name: str) -> Dict[str, Any]:
        releases = self._releases.get(name)
        if releases is None:
            raise WheelInstallError(f"PyPI 查询失败（{name}）：404")
        return {
            "releases": {version: list(files) for version, files in releases.items()}
        }


@pytest.fixture()
def fake_pypi(monkeypatch, tmp_path):
    """挂接假 PyPI：_http_json 走注册表，wheel 下载走 file:// 真实路径。"""
    pypi = _FakePypi()
    monkeypatch.setattr(
        wheel_installer, "_http_json", lambda url: pypi.json_for(url.rsplit("/", 2)[-2])
    )
    return pypi


def _add(
    fake: _FakePypi, tmp_path: Path, name: str, version: str, **kwargs: Any
) -> None:
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir(exist_ok=True)
    filename, sha256 = _build_wheel(wheel_dir, name, version, **kwargs)
    fake.add(name, version, (filename, wheel_dir / filename, sha256))


def test_installs_simple_wheel(fake_pypi, tmp_path):
    _add(fake_pypi, tmp_path, "demo-pkg", "1.2.0")
    site = tmp_path / "site"
    installed = install_wheels(["demo-pkg"], site_dir=str(site))
    assert installed == ["demo-pkg==1.2.0"]
    assert (site / "demo_pkg" / "__init__.py").is_file()
    assert (site / "demo_pkg-1.2.0.dist-info" / "METADATA").is_file()


def test_version_specifier_respected(fake_pypi, tmp_path):
    _add(fake_pypi, tmp_path, "demo-pkg", "1.2.0")
    _add(fake_pypi, tmp_path, "demo-pkg", "2.0.0")
    site = tmp_path / "site"
    installed = install_wheels(["demo-pkg>=1.0,<2"], site_dir=str(site))
    assert installed == ["demo-pkg==1.2.0"]


def test_no_matching_platform_wheel_raises(fake_pypi, tmp_path):
    # 只登记异平台编译 wheel（py3-none-any 缺席）
    _add(fake_pypi, tmp_path, "demo-pkg", "1.0.0", tag="cp39-cp39-win32")
    with pytest.raises(WheelInstallError, match="没有匹配当前平台的 wheel"):
        install_wheels(["demo-pkg"], site_dir=str(tmp_path / "site"))


def test_transitive_dependencies_installed(fake_pypi, tmp_path):
    _add(fake_pypi, tmp_path, "pkg-a", "1.0.0", requires=["pkg-b>=2.0"])
    _add(fake_pypi, tmp_path, "pkg-b", "2.1.0")
    site = tmp_path / "site"
    installed = install_wheels(["pkg-a"], site_dir=str(site))
    assert sorted(installed) == ["pkg-a==1.0.0", "pkg-b==2.1.0"]
    assert (site / "pkg_b" / "__init__.py").is_file()


def test_platform_marker_filtered(fake_pypi, tmp_path):
    _add(
        fake_pypi,
        tmp_path,
        "pkg-a",
        "1.0.0",
        requires=["win-only-dep; sys_platform == 'win32'"],
    )
    _add(fake_pypi, tmp_path, "win-only-dep", "3.0.0")
    site = tmp_path / "site"
    installed = install_wheels(["pkg-a"], site_dir=str(site))
    import sys

    if sys.platform == "win32":
        assert "win-only-dep==3.0.0" in installed
    else:
        assert installed == ["pkg-a==1.0.0"]


def test_extra_gated_dependency(fake_pypi, tmp_path):
    _add(
        fake_pypi,
        tmp_path,
        "pkg-a",
        "1.0.0",
        requires=["extra-dep; extra == 'full'"],
    )
    _add(fake_pypi, tmp_path, "extra-dep", "1.0.0")
    # 无 extras：extra 门控依赖不装
    site_a = tmp_path / "site-a"
    assert install_wheels(["pkg-a"], site_dir=str(site_a)) == ["pkg-a==1.0.0"]
    assert not (site_a / "extra_dep").exists()
    # 带 extras：装
    site_b = tmp_path / "site-b"
    installed = install_wheels(["pkg-a[full]"], site_dir=str(site_b))
    assert sorted(installed) == ["extra-dep==1.0.0", "pkg-a==1.0.0"]


def test_already_satisfied_skips_download(fake_pypi, tmp_path):
    _add(fake_pypi, tmp_path, "demo-pkg", "1.2.0")
    site = tmp_path / "site"
    (site / "demo_pkg-1.2.0.dist-info").mkdir(parents=True)
    assert install_wheels(["demo-pkg"], site_dir=str(site)) == []


def test_sha256_mismatch_raises(fake_pypi, tmp_path):
    _add(fake_pypi, tmp_path, "demo-pkg", "1.2.0")
    # 篡改登记摘要
    for files in fake_pypi._releases["demo-pkg"].values():
        for f in files:
            f["digests"]["sha256"] = "0" * 64
    with pytest.raises(WheelInstallError, match="校验和不匹配"):
        install_wheels(["demo-pkg"], site_dir=str(tmp_path / "site"))


def test_offline_raises(fake_pypi, tmp_path, monkeypatch):
    monkeypatch.setenv("NOVA_OFFLINE", "1")
    _add(fake_pypi, tmp_path, "demo-pkg", "1.2.0")
    with pytest.raises(WheelInstallError, match="NOVA_OFFLINE"):
        install_wheels(["demo-pkg"], site_dir=str(tmp_path / "site"))


def test_non_requirement_targets_skipped(fake_pypi, tmp_path):
    """-e/路径/选项目标不归本通道（冻结形态 path 依赖走 sys.path 挂载）。"""
    assert install_wheels(["-e", "/some/path", "-r"], site_dir=str(tmp_path)) == []


def test_requirements_file(fake_pypi, tmp_path):
    _add(fake_pypi, tmp_path, "demo-pkg", "1.2.0")
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("# 注释\ndemo-pkg>=1.0\n\n", encoding="utf-8")
    installed = install_wheels(
        [], site_dir=str(tmp_path / "site"), requirements_path=str(req_file)
    )
    assert installed == ["demo-pkg==1.2.0"]


@pytest.mark.integration()
def test_real_pillow_install(tmp_path, monkeypatch):
    """真实 PyPI 全链：Pillow 装到临时 site 并可 import（cp312 编译 wheel）。"""
    import importlib.metadata

    # 开发环境本身装有 Pillow（根 pyproject 依赖）——遮蔽"已满足"判定，
    # 强制走真实解析/下载/解包全链
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: (_ for _ in ()).throw(
            importlib.metadata.PackageNotFoundError(name)
        ),
    )
    site = tmp_path / "site"
    installed = install_wheels(["Pillow>=10,<12"], site_dir=str(site))
    assert any(item.startswith("pillow==") for item in installed)
    import sys

    sys.path.insert(0, str(site))
    try:
        import PIL

        assert PIL.__version__
    finally:
        sys.path.remove(str(site))
