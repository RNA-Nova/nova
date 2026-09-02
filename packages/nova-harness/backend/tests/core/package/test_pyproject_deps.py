"""Tests for pyproject.toml dependency parsing."""

import os
import tempfile

import pytest
from nova_harness.core.package import manifest as manifest_module
from nova_harness.core.package.manifest import read_pyproject_dependencies


@pytest.fixture(autouse=True)
def _clear_outside_path_warning_cache():
    """path 依赖越界警告是进程级去重的（模块态 seen 集）——用例间互不影响。"""
    manifest_module._warned_outside_paths.clear()
    yield


@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _write_toml(directory: str, content: str) -> None:
    with open(os.path.join(directory, "pyproject.toml"), "w", encoding="utf-8") as f:
        f.write(content)


def test_poetry_simple_dependency(tmpdir):
    _write_toml(
        tmpdir,
        """
[tool.poetry.dependencies]
python = "^3.11"
requests = "^2.0"
""",
    )
    deps = read_pyproject_dependencies(tmpdir)
    assert any("requests" in d for d in deps)


def test_poetry_path_dependency_editable(tmpdir):
    _write_toml(
        tmpdir,
        """
[tool.poetry.dependencies]
python = "^3.11"
nova-harness = { path = "../nova_harness", develop = true }
""",
    )
    with pytest.warns(UserWarning, match="outside the bundle root"):
        deps = read_pyproject_dependencies(tmpdir)
    assert any(d.startswith("-e ") and "nova_harness" in d for d in deps)


def test_poetry_path_dependency_non_editable(tmpdir):
    _write_toml(
        tmpdir,
        """
[tool.poetry.dependencies]
python = "^3.11"
my-lib = { path = "../my-lib" }
""",
    )
    with pytest.warns(UserWarning, match="outside the bundle root"):
        deps = read_pyproject_dependencies(tmpdir)
    assert any("my-lib" in d and not d.startswith("-e") for d in deps)


def test_poetry_python_excluded(tmpdir):
    _write_toml(
        tmpdir,
        """
[tool.poetry.dependencies]
python = "^3.11"
""",
    )
    deps = read_pyproject_dependencies(tmpdir)
    assert deps == []


def test_pep621_dependency(tmpdir):
    _write_toml(
        tmpdir,
        """
[project]
name = "demo"
dependencies = ["requests>=2.0"]
""",
    )
    deps = read_pyproject_dependencies(tmpdir)
    assert deps == ["requests>=2.0"]


def test_poetry_version_with_extras(tmpdir):
    """extras 必须保留为 name[extras] 后缀，不得静默丢弃。"""
    _write_toml(
        tmpdir,
        """
[tool.poetry.dependencies]
python = "^3.11"
requests = { version = "^2.0", extras = ["security"] }
""",
    )
    deps = read_pyproject_dependencies(tmpdir)
    assert deps == ["requests[security]>=2.0,<3"]


def test_poetry_extras_only_dependency(tmpdir):
    """无 version/path/git 的 extras-only 条目返回 name[extras]，不再整个消失。"""
    _write_toml(
        tmpdir,
        """
[tool.poetry.dependencies]
python = "^3.11"
uvicorn = { extras = ["standard", "cli"] }
""",
    )
    deps = read_pyproject_dependencies(tmpdir)
    assert deps == ["uvicorn[standard,cli]"]


def test_poetry_caret_and_tilde_boundaries(tmpdir):
    """caret/tilde 按 Poetry 语义取上界：最左非零分量（caret）、minor/major（tilde）。"""
    from nova_harness.core.package.manifest import _convert_poetry_version

    assert _convert_poetry_version("^1.2.3") == ">=1.2.3,<2"
    assert _convert_poetry_version("^0.1.2") == ">=0.1.2,<0.2"
    assert _convert_poetry_version("^0.0.3") == ">=0.0.3,<0.0.4"
    assert _convert_poetry_version("^0") == ">=0,<1"
    assert _convert_poetry_version("^0.0") == ">=0.0,<0.1"
    assert _convert_poetry_version("~1.2.3") == ">=1.2.3,<1.3"
    assert _convert_poetry_version("~1.2") == ">=1.2,<1.3"
    assert _convert_poetry_version("~1") == ">=1,<2"
    assert _convert_poetry_version("*") == ""
    assert _convert_poetry_version(">=1.0,<2.0") == ">=1.0,<2.0"


def test_poetry_optional_dependency_skipped(tmpdir):
    """optional = true 的依赖是 opt-in，整体安装时不带入。"""
    _write_toml(
        tmpdir,
        """
[tool.poetry.dependencies]
python = "^3.11"
orjson = { version = "^3.0", optional = true }
""",
    )
    deps = read_pyproject_dependencies(tmpdir)
    assert deps == []


def test_poetry_git_dependency_with_extras(tmpdir):
    """git 依赖同样保留 extras 后缀。"""
    _write_toml(
        tmpdir,
        """
[tool.poetry.dependencies]
python = "^3.11"
nova-ai = { git = "https://github.com/x/nova.git", branch = "main", extras = ["full"] }
""",
    )
    deps = read_pyproject_dependencies(tmpdir)
    assert deps == ["nova-ai[full] @ git+https://github.com/x/nova.git@main"]


def test_no_pyproject(tmpdir):
    deps = read_pyproject_dependencies(tmpdir)
    assert deps == []


def test_poetry_path_dependency_outside_bundle_warns(tmpdir):
    _write_toml(
        tmpdir,
        """
[tool.poetry.dependencies]
python = "^3.11"
nova-harness = { path = "../nova_harness", develop = true }
""",
    )
    with pytest.warns(UserWarning, match="outside the bundle root"):
        deps = read_pyproject_dependencies(tmpdir)
    assert any(d.startswith("-e ") and "nova_harness" in d for d in deps)


def test_poetry_markers_passthrough(tmpdir):
    """version 条目的 markers 以 PEP 508 形式透传，不再静默丢弃。"""
    _write_toml(
        tmpdir,
        """
[tool.poetry.dependencies]
python = "^3.11"
requests = { version = "^2.0", markers = "python_version < '3.12'" }
""",
    )
    deps = read_pyproject_dependencies(tmpdir)
    assert deps == ["requests>=2.0,<3; python_version < '3.12'"]


def test_setup_cfg_install_requires_fallback(tmpdir):
    """无 pyproject 依赖时回退 setup.cfg 的 install_requires（老式项目）。"""
    from nova_harness.core.package.manifest import resolve_package_dependencies

    with open(os.path.join(tmpdir, "setup.cfg"), "w", encoding="utf-8") as f:
        f.write("[options]\ninstall_requires =\n    requests>=2.0\n    click\n")
    deps, requirements_path = resolve_package_dependencies(tmpdir)
    assert deps == ["requests>=2.0", "click"]
    assert requirements_path is None


def test_pyproject_deps_win_over_setup_cfg(tmpdir):
    """pyproject 依赖优先于 setup.cfg install_requires。"""
    from nova_harness.core.package.manifest import resolve_package_dependencies

    _write_toml(
        tmpdir,
        """
[tool.poetry.dependencies]
python = "^3.11"
requests = "^2.0"
""",
    )
    with open(os.path.join(tmpdir, "setup.cfg"), "w", encoding="utf-8") as f:
        f.write("[options]\ninstall_requires =\n    click\n")
    deps, _ = resolve_package_dependencies(tmpdir)
    assert deps == ["requests>=2.0,<3"]
