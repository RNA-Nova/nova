"""Tests for package_manager/manifest.py."""

from nova_harness.package.manifest import (
    read_manifest,
    read_requirements,
)


def _write_pyproject(path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_read_poetry_manifest_without_nova(tmp_path):
    _write_pyproject(
        tmp_path / "pyproject.toml",
        """[tool.poetry]
name = "legacy-pkg"
version = "1.2.3"
description = "old style"
authors = ["nova"]
""",
    )
    manifest = read_manifest(str(tmp_path))
    assert manifest.name == "legacy-pkg"
    assert manifest.version == "1.2.3"
    assert manifest.description == "old style"
    assert manifest.author == "nova"
    assert manifest.nova is None


def test_read_modern_manifest(tmp_path):
    _write_pyproject(
        tmp_path / "pyproject.toml",
        """[tool.poetry]
name = "modern-pkg"
version = "2.0.0"
description = "new style"
authors = ["nova"]

[tool.nova]
agents = ["./agents/coding"]
tools = ["./tools/bash"]
auto_install_dependencies = false
""",
    )
    manifest = read_manifest(str(tmp_path))
    assert manifest.name == "modern-pkg"
    assert manifest.nova is not None
    assert manifest.nova.agents == ["./agents/coding"]
    assert manifest.nova.tools == ["./tools/bash"]
    assert manifest.nova.auto_install_dependencies is False


def test_read_manifest_missing(tmp_path):
    manifest = read_manifest(str(tmp_path))
    assert manifest.name is None
    assert manifest.version == "0.0.0"


def test_read_requirements(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "requests>=2.0\n\n# comment\nhttpx\n",
        encoding="utf-8",
    )
    assert read_requirements(str(tmp_path)) == ["requests>=2.0", "httpx"]


def test_read_requirements_missing(tmp_path):
    assert read_requirements(str(tmp_path)) == []


def test_manifest_ignores_root_dependencies(tmp_path):
    _write_pyproject(
        tmp_path / "pyproject.toml",
        """[tool.poetry]
name = "x"

[tool.poetry.dependencies]
python = ">=3.9"
requests = "^2.0"
""",
    )
    manifest = read_manifest(str(tmp_path))
    assert not hasattr(manifest, "dependencies")


def test_read_manifest_binary_dependencies(tmp_path):
    """[tool.nova] 的二进制依赖字段（wheel 条目 + 系统条目）。"""
    _write_pyproject(
        tmp_path / "pyproject.toml",
        """[tool.poetry]
name = "bin-pkg"

[tool.nova]
tools = ["./tools/grep"]
binary_dependencies = { rg = "ripgrep" }
binary_system_dependencies = ["fd"]
""",
    )
    manifest = read_manifest(str(tmp_path))
    assert manifest.nova is not None
    assert manifest.nova.binary_dependencies == {"rg": "ripgrep"}
    assert manifest.nova.binary_system_dependencies == ["fd"]


def test_read_manifest_binary_managed_dependencies(tmp_path):
    """[tool.nova] 的自管理二进制字段（框架注册表条目）。"""
    _write_pyproject(
        tmp_path / "pyproject.toml",
        """[tool.poetry]
name = "managed-bin-pkg"

[tool.nova]
tools = ["./tools/find"]
binary_managed_dependencies = ["fd"]
""",
    )
    manifest = read_manifest(str(tmp_path))
    assert manifest.nova is not None
    assert manifest.nova.binary_managed_dependencies == ["fd"]


def test_detect_platform_key_shape():
    """平台键形态：linux musl 键带 -musl 后缀（若当前是 musl 系统）。"""
    from nova_harness.package.binaries.manager import detect_platform_key

    key = detect_platform_key()
    assert key is not None
    normalized = key.removesuffix("-musl")
    platform_part, arch = normalized.rsplit("-", 1)
    assert platform_part in ("darwin", "linux", "windows")
    assert arch in ("x86_64", "aarch64")
