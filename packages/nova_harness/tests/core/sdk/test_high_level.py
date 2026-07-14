"""
sdk/high_level.py 测试。
"""

from pathlib import Path
from unittest.mock import patch

from nova_harness.core.sdk import list_installed_agents


def test_list_installed_agents(tmp_path: Path) -> None:
    """通过已安装包发现 agent 配置。"""
    pkg_dir = tmp_path / "nova-coding-agent"
    agents_dir = pkg_dir / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "coding").mkdir()
    (agents_dir / "coding" / "description.md").write_text(
        "Coding agent", encoding="utf-8"
    )
    (agents_dir / "empty").mkdir()

    with patch("nova_harness.core.sdk.PackageManager") as mock_pm_cls:
        mock_pm = mock_pm_cls.return_value
        mock_pm.list.return_value = [
            _fake_metadata(pkg_dir),
        ]
        agents = list_installed_agents()

    assert agents == ["coding"]


def test_list_installed_agents_no_packages(tmp_path: Path) -> None:
    """没有已安装包时返回空列表。"""
    with patch("nova_harness.core.sdk.PackageManager") as mock_pm_cls:
        mock_pm = mock_pm_cls.return_value
        mock_pm.list.return_value = []
        assert list_installed_agents() == []


def _fake_metadata(pkg_dir: Path):
    from datetime import datetime, timezone

    from nova_harness.core.types.package_manager import PackageMetadata

    return PackageMetadata(
        name="nova-coding-agent",
        version="1.0.0",
        description="",
        source="path:.",
        install_path=str(pkg_dir),
        installed_at=datetime.now(timezone.utc).isoformat(),
    )
