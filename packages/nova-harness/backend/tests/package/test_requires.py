"""包间依赖（requires）测试。

锁定：A 型（[tool.nova] requires）与 B 型（package.json "nova".requires）
的声明读取；安装时校验（缺失拒绝/满足放行/跨 scope 合并视图）；卸载
守护（被依赖拒绝/依赖方移除后放行）；元数据 requires 透出。
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from nova_harness.package import PackageManager
from nova_harness.package.manifest import read_manifest


@pytest.fixture
def pm(tmp_path):
    return PackageManager(
        agent_dir=str(tmp_path / "agent"),
        cwd=str(tmp_path),
        project_trusted=True,
    )


@pytest.fixture(autouse=True)
def no_python_dependency_install():
    with patch("nova_harness.package.install.installer.install_dependencies"):
        with patch("nova_harness.package.install.installer.install_package"):
            yield


def _make_a_pkg(root: Path, name: str, requires=None) -> Path:
    """最小合法 A 型包（agents 类目 + 可选 requires）。"""
    root.mkdir(parents=True, exist_ok=True)
    agents_dir = root / "agents"
    agents_dir.mkdir()
    (agents_dir / "a.yaml").write_text("description: x\npersona: [personas/r.md]\n")
    (agents_dir / "personas").mkdir()
    (agents_dir / "personas" / "r.md").write_text("role")
    req_line = f"requires = {requires!r}".replace("'", '"') if requires else ""
    (root / "pyproject.toml").write_text(
        f'[tool.poetry]\nname = "{name}"\nversion = "1.0.0"\n\n'
        f'[tool.nova]\nagents = ["./agents"]\n{req_line}\n',
        encoding="utf-8",
    )
    return root


def _make_b_pkg(root: Path, name: str, requires=None) -> Path:
    """最小合法 B 型包（package.json 身份证 + tui 段 + 可选 nova.requires）。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "tui" / "tools").mkdir(parents=True)
    (root / "tui" / "tools" / "x.ts").write_text(
        "export default function () { return []; }\n", encoding="utf-8"
    )
    import json

    payload = {"name": name, "version": "1.0.0", "description": "b"}
    if requires:
        payload["nova"] = {"requires": requires}
    (root / "package.json").write_text(json.dumps(payload), encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# 声明读取
# ---------------------------------------------------------------------------


def test_a_type_manifest_reads_requires(tmp_path):
    pkg = _make_a_pkg(tmp_path / "pkg", "a-pkg", requires=["dep-a", "dep-b"])
    manifest = read_manifest(str(pkg))
    assert manifest.nova is not None
    assert manifest.nova.requires == ["dep-a", "dep-b"]


def test_b_type_manifest_reads_requires(tmp_path):
    pkg = _make_b_pkg(tmp_path / "pkg", "b-pkg", requires=["dep-a"])
    manifest = read_manifest(str(pkg))
    assert manifest.nova is not None
    assert manifest.nova.requires == ["dep-a"]


def test_b_type_without_nova_key_has_no_requires(tmp_path):
    pkg = _make_b_pkg(tmp_path / "pkg", "b-pkg")
    manifest = read_manifest(str(pkg))
    assert manifest.nova is None


# ---------------------------------------------------------------------------
# 安装时校验
# ---------------------------------------------------------------------------


def test_install_rejected_when_requires_missing(pm, tmp_path):
    pkg = _make_a_pkg(tmp_path / "pkg", "needy-pkg", requires=["ghost-pkg"])
    with pytest.raises(ValueError, match="ghost-pkg"):
        pm.install(str(pkg))
    # 拒绝发生在副作用之前：包未落盘
    assert pm.list() == []


def test_install_allowed_when_requires_satisfied(pm, tmp_path):
    dep = _make_a_pkg(tmp_path / "dep", "dep-pkg")
    pm.install(str(dep))
    needy = _make_a_pkg(tmp_path / "needy", "needy-pkg", requires=["dep-pkg"])
    meta = pm.install(str(needy))
    assert meta.requires == ["dep-pkg"]
    assert sorted(p.name for p in pm.list()) == ["dep-pkg", "needy-pkg"]


def test_requires_satisfied_across_scopes(pm, tmp_path):
    """合并视图：user scope 已装的依赖满足 project scope 的安装。"""
    dep = _make_a_pkg(tmp_path / "dep", "dep-pkg")
    pm.install(str(dep))  # user scope
    needy = _make_a_pkg(tmp_path / "needy", "needy-pkg", requires=["dep-pkg"])
    meta = pm.install(str(needy), local=True)  # project scope
    assert meta.requires == ["dep-pkg"]


def test_b_type_install_enforces_requires(pm, tmp_path):
    pkg = _make_b_pkg(tmp_path / "pkg", "b-pkg", requires=["ghost-pkg"])
    with pytest.raises(ValueError, match="ghost-pkg"):
        pm.install(str(pkg))


# ---------------------------------------------------------------------------
# 卸载守护
# ---------------------------------------------------------------------------


def test_uninstall_blocked_when_required_by_other(pm, tmp_path):
    dep = _make_a_pkg(tmp_path / "dep", "dep-pkg")
    pm.install(str(dep))
    needy = _make_a_pkg(tmp_path / "needy", "needy-pkg", requires=["dep-pkg"])
    pm.install(str(needy))

    with pytest.raises(ValueError, match="needy-pkg"):
        pm.uninstall("dep-pkg")
    # 两包均未被移除
    assert sorted(p.name for p in pm.list()) == ["dep-pkg", "needy-pkg"]


def test_uninstall_allowed_after_dependent_removed(pm, tmp_path):
    dep = _make_a_pkg(tmp_path / "dep", "dep-pkg")
    pm.install(str(dep))
    needy = _make_a_pkg(tmp_path / "needy", "needy-pkg", requires=["dep-pkg"])
    pm.install(str(needy))

    result = pm.uninstall("needy-pkg")
    assert result.removed is True
    result = pm.uninstall("dep-pkg")
    assert result.removed is True
    assert pm.list() == []
