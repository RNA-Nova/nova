"""安装链路回归测试：边界行为的锁定。"""

from pathlib import Path
from unittest.mock import patch

import pytest
from nova_harness.core.package import PackageManager
from nova_harness.core.package.install.store import (
    dist_info_dir,
    read_dist_info,
    write_dist_info,
)
from nova_harness.core.package.source.spec import parse_source


@pytest.fixture
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


def _make_pkg(path: Path, name: str = "pkg-x", build_system: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    build = '[build-system]\nrequires = ["setuptools"]\n\n' if build_system else ""
    (path / "pyproject.toml").write_text(
        f'{build}[tool.poetry]\nname = "{name}"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    skill = path / "skills" / "s"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text("---\nname: s\ndescription: d\n---")
    return path


def test_install_rejects_source_containing_install_root(pm, tmp_path):
    """源目录包含安装目标（祖先目录）时直接拒绝——copytree 祖先→后代会无限递归。"""
    # tmp_path 下的 agent/ 是安装根；把 tmp_path 本身当包安装时，
    # 目标 tmp_path/agent/packages/path/pkg-x 位于源目录内部。
    _make_pkg(tmp_path)
    with pytest.raises(ValueError, match="recurse"):
        pm.install(str(tmp_path))


@pytest.mark.asyncio
async def test_update_by_source_preserves_editable_and_filters(pm, tmp_path):
    """按 source 更新必须保留原 spec 的 editable 与 filters（settings 是唯一权威）。"""
    pkg = _make_pkg(tmp_path / "pkg")
    installer = pm._installer(False)
    installer.install_and_persist(
        {"source": str(pkg), "editable": True, "skills": ["s"]}
    )

    await pm.update(str(pkg))

    specs = pm.settings_manager.get_package_sources(
        local=False, base_dir=str(installer.install_dir)
    )
    assert len(specs) == 1
    spec = specs[0]
    assert isinstance(spec, dict)
    assert spec.get("editable") is True
    assert spec.get("skills") == ["s"]


def test_uninstall_by_source_defers_python_package_removal(pm, tmp_path):
    """按 source 卸载时 uninstall_python_package=False 必须透传到按名卸载路径——
    跨 scope 卸载流程依赖该标志把 Python 包卸载推迟到引用计数统计之后。"""
    pkg = _make_pkg(tmp_path / "pkg", build_system=True)
    installer = pm._installer(False)
    meta = installer.install_and_persist(str(pkg))
    assert meta.package_name  # 自安装边界：name + build-system

    with patch(
        "nova_harness.core.package.install.installer.uninstall_package"
    ) as mock_uninstall:
        removed = installer.uninstall(str(pkg), uninstall_python_package=False)

    assert removed is True
    mock_uninstall.assert_not_called()


def test_write_dist_info_clears_stale_package_name(tmp_path):
    """重装为"不再是 Python 包"的包时，dist-info 中残留的 package_name 必须清除。"""
    install_path = tmp_path / "pkg"
    install_path.mkdir()
    source_obj = parse_source(str(install_path))

    write_dist_info(
        str(install_path),
        source_obj,
        str(install_path),
        editable=False,
        package_name="foo",
    )
    assert read_dist_info(str(install_path)).package_name == "foo"

    write_dist_info(
        str(install_path),
        source_obj,
        str(install_path),
        editable=False,
        package_name="",
    )
    dist = read_dist_info(str(install_path))
    assert dist is not None
    assert dist.package_name == ""
    assert not (dist_info_dir(str(install_path)) / "package_name").exists()


def test_install_binary_only_package_reaches_managed_ensure(pm, tmp_path):
    """仅声明 binary_managed_dependencies（无 pip 依赖）的包也必须触发下载确保。

    回归：Phase 1 曾以 ``deps or requirements_path`` 为进入条件，
    纯二进制包（deps 为空）会整体跳过自管理二进制的 ensure。
    """
    pkg = tmp_path / "pkg-bin"
    pkg.mkdir()
    (pkg / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "pkg-bin"\nversion = "1.0.0"\n\n'
        '[tool.nova]\nskills = ["./skills/s"]\n'
        'binary_managed_dependencies = ["fd"]\n',
        encoding="utf-8",
    )
    skill = pkg / "skills" / "s"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: s\ndescription: d\n---")

    with patch(
        "nova_harness.core.package.install.installer.ensure_binary"
    ) as mock_ensure:
        mock_ensure.return_value = "/fake/bin/fd"
        pm.install(str(pkg))

    assert mock_ensure.call_count == 1
    assert mock_ensure.call_args[0][0] == "fd"
