"""npm 依赖阶段（安装流程 Phase 4）回归测试。

锁定：检测规则（前端半区 package.json——A 型复合包探测 frontend/，
B 型纯 TS 包探测包根；包根遗留 package.json 不触发，不做双轨）、
命令选择（lock → npm ci）、目录选择（editable 源目录 / copy 副本）、
跳过条件（npm 缺失/离线）、失败解耦（npm 失败不阻断安装）。
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from nova_harness.core.package import PackageManager


@pytest.fixture
def pm(tmp_path):
    return PackageManager(
        agent_dir=str(tmp_path / "agent"),
        cwd=str(tmp_path),
        project_trusted=True,
    )


@pytest.fixture(autouse=True)
def no_python_dependency_install():
    """避免测试执行真实 pip/uv 命令。"""
    with patch("nova_harness.core.package.install.installer.install_dependencies"):
        with patch("nova_harness.core.package.install.installer.install_package"):
            yield


@pytest.fixture
def npm_calls():
    """捕获 subprocess.run 的调用参数。"""
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, **kwargs})

        class _Result:
            returncode = 0

        return _Result()

    return calls, _fake_run


def _make_pkg(
    root: Path,
    name: str = "pkg-npm",
    *,
    with_package_json: bool = True,
    with_lock: bool = False,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[tool.poetry]\nname = "{name}"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    skill = root / "skills" / "s"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text("---\nname: s\ndescription: d\n---")
    if with_package_json or with_lock:
        # A 型复合包：npm 清单在前端半区 frontend/ 下
        frontend = root / "frontend"
        frontend.mkdir(parents=True, exist_ok=True)
        if with_package_json:
            (frontend / "package.json").write_text(
                '{"name": "@pkg-npm/ui", "dependencies": {}}', encoding="utf-8"
            )
        if with_lock:
            (frontend / "package-lock.json").write_text("{}", encoding="utf-8")
    return root


def test_no_package_json_skips_npm(pm, tmp_path, npm_calls):
    """无 frontend/package.json 的包不触发 npm。"""
    calls, fake_run = npm_calls
    pkg = _make_pkg(tmp_path / "pkg", with_package_json=False)
    with patch("subprocess.run", fake_run):
        pm.install(str(pkg))
    assert calls == []


def test_legacy_root_package_json_does_not_trigger_npm(pm, tmp_path, npm_calls):
    """A 型包根的遗留 package.json 不再触发 npm（不做双轨）。"""
    calls, fake_run = npm_calls
    pkg = _make_pkg(tmp_path / "pkg", with_package_json=False)
    (pkg / "package.json").write_text(
        '{"name": "legacy-root", "dependencies": {}}', encoding="utf-8"
    )
    with patch("subprocess.run", fake_run):
        pm.install(str(pkg))
    assert calls == []


def test_lockfile_uses_npm_ci_in_install_copy(pm, tmp_path, npm_calls):
    """有 lockfile → npm ci --omit=dev；copy 模式 cwd = 安装副本的 frontend/。"""
    calls, fake_run = npm_calls
    pkg = _make_pkg(tmp_path / "pkg", with_lock=True)
    with patch("subprocess.run", fake_run):
        meta = pm.install(str(pkg))
    assert len(calls) == 1
    cmd = calls[0]["cmd"]
    assert cmd[1] == "ci"
    assert "--omit=dev" in cmd
    assert calls[0]["cwd"] == str(Path(meta.install_path) / "frontend")
    # cwd 绝不是源目录（必须在副本内装配）
    assert calls[0]["cwd"] != str(pkg)


def test_no_lockfile_uses_npm_install(pm, tmp_path, npm_calls):
    """无 lockfile → npm install。"""
    calls, fake_run = npm_calls
    pkg = _make_pkg(tmp_path / "pkg")
    with patch("subprocess.run", fake_run):
        pm.install(str(pkg))
    assert calls[0]["cmd"][1] == "install"


def test_editable_runs_npm_in_source_dir(pm, tmp_path, npm_calls):
    """editable 模式 npm 在源目录的 frontend/ 执行（与 pip -e 同语义）。"""
    calls, fake_run = npm_calls
    pkg = _make_pkg(tmp_path / "pkg", name="pkg-editable")
    with patch("subprocess.run", fake_run):
        pm.install({"source": str(pkg), "editable": True})
    assert len(calls) == 1
    assert calls[0]["cwd"] == str(pkg / "frontend")


def test_missing_npm_warns_but_does_not_block(pm, tmp_path, npm_calls, caplog):
    """npm 不存在 → 警告并跳过，安装照常成功（TS 资产降级不阻断）。"""
    calls, fake_run = npm_calls
    pkg = _make_pkg(tmp_path / "pkg")
    with patch("shutil.which", return_value=None):
        with patch("subprocess.run", fake_run):
            meta = pm.install(str(pkg))
    assert calls == []
    assert meta.install_path
    assert any("未找到 npm" in r.message for r in caplog.records)


def test_offline_mode_skips_npm(pm, tmp_path, npm_calls, monkeypatch):
    """NOVA_OFFLINE → 跳过 npm 阶段。"""
    calls, fake_run = npm_calls
    monkeypatch.setenv("NOVA_OFFLINE", "1")
    pkg = _make_pkg(tmp_path / "pkg")
    with patch("subprocess.run", fake_run):
        pm.install(str(pkg))
    assert calls == []


def test_npm_failure_does_not_block_install(pm, tmp_path, caplog):
    """npm 执行失败 → 警告但安装成功（失败解耦：能力部分不受影响）。"""
    import subprocess as sp

    def _failing_run(cmd, **kwargs):
        raise sp.CalledProcessError(1, cmd)

    pkg = _make_pkg(tmp_path / "pkg")
    with patch("subprocess.run", _failing_run):
        meta = pm.install(str(pkg))
    assert meta.install_path
    assert any("npm 依赖安装失败" in r.message for r in caplog.records)
