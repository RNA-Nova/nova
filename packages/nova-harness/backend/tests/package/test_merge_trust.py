"""pkgList 合并视图的信任门控测试。

锁定：``list_with_resources(local=False)``（user+project 合并，project 优先
去重）在项目不被信任时整组剔除 project 级——否则 project 副本按身份去重
挤掉 user 副本后又被前端 trust 过滤，出现"装了却一个都不加载"的窗口
（用户现场：cwd 位于未信任项目内时 bundle 前端贡献全灭）。
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from nova_harness.package import PackageManager


@pytest.fixture(autouse=True)
def no_python_dependency_install():
    with patch("nova_harness.package.install.installer.install_dependencies"):
        with patch("nova_harness.package.install.installer.install_package"):
            yield


def _make_pkg(root: Path, name: str) -> Path:
    """最小合法 A 型包。"""
    agents_dir = root / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "a.yaml").write_text("description: x\npersona: [personas/r.md]\n")
    (agents_dir / "personas").mkdir()
    (agents_dir / "personas" / "r.md").write_text("role")
    (root / "pyproject.toml").write_text(
        f'[tool.poetry]\nname = "{name}"\nversion = "1.0.0"\n\n'
        f'[tool.nova]\nagents = ["./agents"]\n',
        encoding="utf-8",
    )
    return root


def _pm(tmp_path: Path, trusted: bool) -> PackageManager:
    return PackageManager(
        agent_dir=str(tmp_path / "agent"),
        cwd=str(tmp_path),
        project_trusted=trusted,
    )


def _install_both_scopes(pm: PackageManager, tmp_path: Path) -> None:
    pkg = _make_pkg(tmp_path / "src" / "pkg-a", "pkg-a")
    pm.install(str(pkg))  # user scope
    pm.install(str(pkg), local=True)  # project scope（同一 source——身份碰撞）


def test_untrusted_project_yields_user_copy(tmp_path):
    """项目不被信任：合并视图只剩 user 副本（scope=user），project 整组剔除。"""
    pm = _pm(tmp_path, trusted=False)
    _install_both_scopes(pm, tmp_path)

    views = pm.list_with_resources(local=False)

    assert len(views) == 1
    view = next(iter(views.values()))
    assert view.name == "pkg-a"
    assert view.scope == "user"


def test_trusted_project_shadows_user_copy(tmp_path):
    """项目被信任：project 副本按身份去重胜出（scope=project）。"""
    pm = _pm(tmp_path, trusted=True)
    _install_both_scopes(pm, tmp_path)

    views = pm.list_with_resources(local=False)

    assert len(views) == 1
    view = next(iter(views.values()))
    assert view.scope == "project"


def test_untrusted_project_keeps_project_only_listing(tmp_path):
    """local=True 是管理视图：即使不信任也照常列出 project 级（装/卸是主动行为）。"""
    pm = _pm(tmp_path, trusted=False)
    _install_both_scopes(pm, tmp_path)

    views = pm.list_with_resources(local=True)

    assert len(views) == 1
    assert next(iter(views.values())).scope == "project"
