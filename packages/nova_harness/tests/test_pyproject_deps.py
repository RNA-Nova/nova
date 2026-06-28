"""Tests for pyproject.toml dependency parsing."""

import os
import tempfile

import pytest

from nova_harness.core.package.pyproject_deps import read_pyproject_dependencies


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


def test_no_pyproject(tmpdir):
    deps = read_pyproject_dependencies(tmpdir)
    assert deps == []
