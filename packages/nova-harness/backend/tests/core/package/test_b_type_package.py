"""B 型纯 TS 包（package.json 身份证）的 manifest 与安装链路测试。

锁定：身份证读取（name/version/scope 剥离）、pyproject 优先、安装时跳过
Python 阶段（无自安装、npm 阶段照跑）、dist-info 落盘、validate 放行与拒绝。
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from nova_harness.core.package import PackageManager
from nova_harness.core.package.install.store import read_dist_info
from nova_harness.core.package.manifest import read_manifest


@pytest.fixture
def pm(tmp_path):
    return PackageManager(
        agent_dir=str(tmp_path / "agent"),
        cwd=str(tmp_path),
        project_trusted=True,
    )


@pytest.fixture(autouse=True)
def no_python_dependency_install():
    with patch("nova_harness.core.package.install.installer.install_dependencies"):
        with patch("nova_harness.core.package.install.installer.install_package"):
            yield


def _make_ts_pkg(
    root: Path, name: str = "@scope/ts-pkg", version: str = "2.0.0"
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(
        '{"name": "%s", "version": "%s", "description": "pure ts", "dependencies": {}}'
        % (name, version),
        encoding="utf-8",
    )
    (root / "tui" / "tools").mkdir(parents=True)
    (root / "tui" / "tools" / "x.ts").write_text(
        "export default function () { return []; }\n", encoding="utf-8"
    )
    return root


def test_read_manifest_from_package_json(tmp_path):
    """B 型：无 pyproject.toml 时从 package.json 读身份（scope 剥离）。"""
    pkg = _make_ts_pkg(tmp_path / "pkg")
    manifest = read_manifest(str(pkg))
    assert manifest.name == "ts-pkg"  # @scope/ts-pkg → ts-pkg
    assert manifest.version == "2.0.0"
    assert manifest.description == "pure ts"
    assert manifest.nova is None  # B 型没有 [tool.nova] 段


def test_pyproject_takes_precedence_over_package_json(tmp_path):
    """A 型：pyproject.toml 与 package.json 并存时，身份以 pyproject 为准。"""
    pkg = _make_ts_pkg(tmp_path / "pkg")
    (pkg / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "py-name"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    manifest = read_manifest(str(pkg))
    assert manifest.name == "py-name"


def test_b_type_install_skips_python_but_runs_npm(pm, tmp_path):
    """B 型安装：无自安装（非 Python 包），npm 阶段照跑，dist-info 落盘。"""
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, **kwargs})

        class _Result:
            returncode = 0

        return _Result()

    pkg = _make_ts_pkg(tmp_path / "pkg")
    with patch("subprocess.run", _fake_run):
        meta = pm.install(str(pkg))

    assert meta.name == "ts-pkg"
    assert meta.version == "2.0.0"
    assert meta.package_name == ""  # 无 Python 自安装
    # npm 阶段在副本内执行（copy 模式）
    assert len(calls) == 1
    assert calls[0]["cwd"] == meta.install_path
    # dist-info 落盘（sibling 目录，read_dist_info 直接吃 install_path）
    dist = read_dist_info(meta.install_path)
    assert dist is not None


def test_validate_accepts_pure_ts_package(pm, tmp_path):
    pkg = _make_ts_pkg(tmp_path / "pkg")
    assert pm.validate(str(pkg)) == []


def test_validate_rejects_empty_package(pm, tmp_path):
    """空目录（无能力类目且无 package.json+tui/）被拒绝。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "empty"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    issues = pm.validate(str(empty))
    assert issues
    assert "pure-TS package" in issues[0]
