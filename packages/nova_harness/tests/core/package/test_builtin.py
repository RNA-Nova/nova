"""内建官方包通道（core/package/builtin.py）测试。

冻结形态的首启落地 + settings 登记 + 幂等 + 版本刷新 + 卸载尊重；
非冻结形态零动作。内建清单只有 nova-base（产品定案：壳内建、能力按需装）。
全部用临时 _MEIPASS 假包 + 临时 agentDir。
"""

import sys
from pathlib import Path

import pytest
from nova_harness.core.config.settings.manager import SettingsManager
from nova_harness.core.package import builtin


def _make_bundled(tmp_path: Path, name: str, version: str) -> Path:
    """构造一个假的"二进制携带 bundle"目录。"""
    src = tmp_path / "meipass" / "bundles" / name
    (src / "backend" / "tools").mkdir(parents=True)
    (src / "pyproject.toml").write_text(
        f'[tool.poetry]\nname = "{name.replace("_", "-")}"\nversion = "{version}"\n'
        '[tool.nova]\ntools = ["./backend/tools/hello.py"]\n',
        encoding="utf-8",
    )
    (src / "backend" / "marker.txt").write_text(version, encoding="utf-8")
    # 安装器拒装零资源包——给一个最小工具让假包可安装
    (src / "backend" / "tools" / "hello.py").write_text(
        'class Tool:\n    name = "hello"\n    description = "x"\n'
        '    parameters = {"type": "object", "properties": {}}\n',
        encoding="utf-8",
    )
    return src


def _freeze(monkeypatch, tmp_path: Path) -> Path:
    """把 sys 伪装成冻结形态，_MEIPASS 指向临时目录。返回 agent_dir。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "meipass"), raising=False)
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    # 与生产同构：settings 路径归一化的基准目录取 env 的 agent 根
    # （get_agent_dir()），夹具必须把 NOVA_AGENT_DIR 一并指过去
    monkeypatch.setenv("NOVA_AGENT_DIR", str(agent_dir))
    return agent_dir


def _settings(agent_dir: Path) -> SettingsManager:
    return SettingsManager.create(str(agent_dir / "proj"), str(agent_dir))


def test_not_frozen_noop(tmp_path, monkeypatch):
    """开发态（无 sys.frozen）：零动作，不落盘不登记。"""
    monkeypatch.delattr(sys, "frozen", raising=False)
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    actions = builtin.ensure_builtin_packages(_settings(agent_dir), str(agent_dir))
    assert actions == []
    assert not (agent_dir / "builtin").exists()


def test_frozen_first_boot_lands_and_registers(tmp_path, monkeypatch):
    """首启：nova-base 落地 builtin/ + 登记进 settings + 播种标记。"""
    _make_bundled(tmp_path, "nova_base", "0.1.0")
    agent_dir = _freeze(monkeypatch, tmp_path)
    sm = _settings(agent_dir)

    actions = builtin.ensure_builtin_packages(sm, str(agent_dir))

    assert "landed nova_base@0.1.0" in actions
    assert "registered nova_base" in actions
    assert (agent_dir / "builtin" / "nova_base" / "backend" / "marker.txt").exists()
    sources = [str(getattr(s, "source", s)) for s in sm.get_package_sources()]
    # settings 持久化统一 posix 分隔符——断言按名字判定（Windows 不假设分隔符）
    assert any("nova_base" in s and "builtin" in s.replace("\\", "/") for s in sources)


def test_frozen_second_boot_idempotent(tmp_path, monkeypatch):
    """二次启动：版本一致零动作。"""
    _make_bundled(tmp_path, "nova_base", "0.1.0")
    agent_dir = _freeze(monkeypatch, tmp_path)
    sm = _settings(agent_dir)
    builtin.ensure_builtin_packages(sm, str(agent_dir))

    actions = builtin.ensure_builtin_packages(sm, str(agent_dir))
    assert actions == []


def test_frozen_version_upgrade_relands(tmp_path, monkeypatch):
    """二进制内 bundle 版本变了：重落地但不回补登记（条目已存在）。"""
    _make_bundled(tmp_path, "nova_base", "0.1.0")
    agent_dir = _freeze(monkeypatch, tmp_path)
    sm = _settings(agent_dir)
    builtin.ensure_builtin_packages(sm, str(agent_dir))

    # 模拟升级：换新版 bundle
    import shutil

    shutil.rmtree(tmp_path / "meipass")
    _make_bundled(tmp_path, "nova_base", "0.2.0")

    actions = builtin.ensure_builtin_packages(sm, str(agent_dir))
    assert "landed nova_base@0.2.0" in actions
    assert "registered nova_base" not in actions  # 已登记，不重复
    assert (agent_dir / "builtin" / "nova_base" / ".builtin-version").read_text(
        encoding="utf-8"
    ).strip() == "0.2.0"


def test_frozen_respects_user_removal(tmp_path, monkeypatch):
    """播种后用户从 settings 移除条目：不再回补。"""
    _make_bundled(tmp_path, "nova_base", "0.1.0")
    agent_dir = _freeze(monkeypatch, tmp_path)
    sm = _settings(agent_dir)
    builtin.ensure_builtin_packages(sm, str(agent_dir))

    # 用户卸载（从清单移除）
    sm.set_packages([])
    actions = builtin.ensure_builtin_packages(sm, str(agent_dir))
    assert "registered nova_base" not in actions
    assert sm.get_package_sources() == []


def test_frozen_missing_bundled_dir_skips(tmp_path, monkeypatch):
    """构建期未携带：跳过不阻断。"""
    agent_dir = _freeze(monkeypatch, tmp_path)  # meipass 里没有任何 bundles
    sm = _settings(agent_dir)
    actions = builtin.ensure_builtin_packages(sm, str(agent_dir))
    assert actions == []


def test_frozen_builtin_visible_to_pkg_domain_after_reconcile(tmp_path, monkeypatch):
    """登记后经 resolve_resources 物化进安装仓——pkg 域（list/requires 门）可见。

    回归钉：内建包只登记进 settings 时 list() 为空（安装仓视图只扫
    packages/ 族目录），pkg 作为首触点装带 requires 的官方包会被误拒。
    pkg CLI 在冻结形态按"落地 + 登记 + 物化"三步走（cli/package.py）。
    """
    import asyncio

    from nova_harness.core.package.manager import PackageManager

    _make_bundled(tmp_path, "nova_base", "0.1.0")
    agent_dir = _freeze(monkeypatch, tmp_path)
    sm = _settings(agent_dir)
    builtin.ensure_builtin_packages(sm, str(agent_dir))

    pm = PackageManager(agent_dir=str(agent_dir), settings_manager=sm)
    asyncio.run(pm.resolve_resources())

    names = [p.name for p in pm.list()]
    assert "nova-base" in names
    # requires 门不再误报缺失
    assert pm._requires_missing(["nova-base"]) == []

