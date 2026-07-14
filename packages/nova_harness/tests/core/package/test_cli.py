"""
nova-pkg CLI 单元测试。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova_harness.cli.package import main
from nova_harness.core.types.package_manager import PackageMetadata, PackageView


def _metadata(name="pkg", version="1.0.0") -> PackageMetadata:
    return PackageMetadata(
        name=name,
        version=version,
        description="desc",
        source="/src",
        install_path="/path",
        installed_at="now",
    )


def _resource_metadata(name="res"):
    from nova_harness.core.types.package_manager import ResourceMetadata

    return ResourceMetadata(
        name=name,
        resource_type="agent",
        source="/src",
        install_path="/path",
    )


@patch("nova_harness.cli.package.PackageManager")
def test_list_flat_empty(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.list.return_value = []
    mock_pm_class.return_value = mock_pm

    result = main(["list", "--flat"])
    assert result == 0
    captured = capsys.readouterr()
    assert "No packages installed" in captured.out


@patch("nova_harness.cli.package.PackageManager")
def test_list_flat_with_packages(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.list.return_value = [_metadata("a"), _metadata("t")]
    mock_pm_class.return_value = mock_pm

    result = main(["list", "--flat"])
    assert result == 0
    captured = capsys.readouterr()
    assert "a" in captured.out
    assert "t" in captured.out


@patch("nova_harness.cli.package.PackageManager")
def test_list_package_view(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.list_with_resources.return_value = {
        "my-pkg": PackageView(
            name="my-pkg",
            version="1.0",
            description="pkg desc",
            agents=[_resource_metadata("a")],
            tools=[_resource_metadata("t")],
            skills=[_resource_metadata("s")],
            extensions=[_resource_metadata("e")],
        )
    }
    mock_pm_class.return_value = mock_pm

    result = main(["list"])
    assert result == 0
    captured = capsys.readouterr()
    assert "Package: my-pkg" in captured.out
    assert "pkg desc" in captured.out
    assert "Agents:    a" in captured.out
    assert "Tools:     t" in captured.out
    assert "Skills:    s" in captured.out
    assert "Extensions: e" in captured.out


@patch("nova_harness.cli.package.PackageManager")
def test_list_package_view_empty(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.list_with_resources.return_value = {}
    mock_pm_class.return_value = mock_pm

    result = main(["list"])
    assert result == 0
    assert "No packages installed" in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_install(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.install_and_persist.return_value = _metadata("x")
    mock_pm_class.return_value = mock_pm

    result = main(["install", "/path/to/x"])
    assert result == 0
    mock_pm.install_and_persist.assert_called_once_with(
        "/path/to/x",
        local=False,
        no_deps=False,
        dry_run=False,
        editable=False,
    )
    captured = capsys.readouterr()
    assert "Installed 'x'" in captured.out


@patch("nova_harness.cli.package.PackageManager")
def test_install_editable_flag(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.install_and_persist.return_value = _metadata("x")
    mock_pm_class.return_value = mock_pm

    result = main(["install", "/path/to/x", "--editable"])
    assert result == 0
    mock_pm.install_and_persist.assert_called_once_with(
        "/path/to/x",
        local=False,
        no_deps=False,
        dry_run=False,
        editable=True,
    )
    captured = capsys.readouterr()
    assert "Installed 'x'" in captured.out


@patch("nova_harness.cli.package.PackageManager")
def test_install_editable_flag_rejects_git(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.install_and_persist.side_effect = ValueError(
        "Editable mode only supports path sources"
    )
    mock_pm_class.return_value = mock_pm

    result = main(["install", "git:github.com/user/repo", "--editable"])
    assert result == 1
    mock_pm.install_and_persist.assert_called_once_with(
        "git:github.com/user/repo",
        local=False,
        no_deps=False,
        dry_run=False,
        editable=True,
    )
    assert "only supports path sources" in capsys.readouterr().err


@patch("nova_harness.cli.package.PackageManager")
def test_uninstall_found(mock_pm_class, capsys):
    from nova_harness.core.types.package_manager import UninstallResult

    mock_pm = MagicMock()
    mock_pm.uninstall.return_value = UninstallResult(removed=True)
    mock_pm_class.return_value = mock_pm

    result = main(["uninstall", "x"])
    assert result == 0
    mock_pm.uninstall.assert_called_once_with("x", local=False)
    assert "Uninstalled" in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_uninstall_not_found(mock_pm_class, capsys):
    from nova_harness.core.types.package_manager import UninstallResult

    mock_pm = MagicMock()
    mock_pm.uninstall.return_value = UninstallResult(removed=False)
    mock_pm_class.return_value = mock_pm

    result = main(["uninstall", "x"])
    assert result == 1
    assert "not found" in capsys.readouterr().err


@patch("nova_harness.cli.package.PackageManager")
def test_update(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.update = AsyncMock(return_value=[_metadata("x", "2.0")])
    mock_pm_class.return_value = mock_pm

    result = main(["update", "x"])
    assert result == 0
    mock_pm.update.assert_awaited_once_with("x", local=False)
    assert "Updated 'x'" in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_info_found(mock_pm_class, capsys):
    meta = _metadata("x")
    mock_pm = MagicMock()
    mock_pm.info.return_value = meta
    mock_pm_class.return_value = mock_pm

    result = main(["info", "x"])
    assert result == 0
    captured = capsys.readouterr()
    assert "Name:        x" in captured.out


@patch("nova_harness.cli.package.PackageManager")
def test_info_not_found(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.info.return_value = None
    mock_pm_class.return_value = mock_pm

    result = main(["info", "x"])
    assert result == 1
    assert "not found" in capsys.readouterr().err


@patch("nova_harness.cli.package.PackageManager")
def test_validate_ok(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.validate.return_value = []
    mock_pm_class.return_value = mock_pm

    result = main(["validate", "/path"])
    assert result == 0
    mock_pm.validate.assert_called_once_with("/path", local=False)
    assert "is a valid package" in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_validate_failed(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.validate.return_value = ["missing foo"]
    mock_pm_class.return_value = mock_pm

    result = main(["validate", "/path"])
    assert result == 1
    assert "Validation failed" in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_json_list_flat(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.list.return_value = [_metadata("a")]
    mock_pm_class.return_value = mock_pm

    result = main(["list", "--json", "--flat"])
    assert result == 0
    captured = capsys.readouterr()
    assert '"name": "a"' in captured.out


@patch("nova_harness.cli.package.PackageManager")
def test_json_uninstall(mock_pm_class, capsys):
    from nova_harness.core.types.package_manager import UninstallResult

    mock_pm = MagicMock()
    mock_pm.uninstall.return_value = UninstallResult(removed=True)
    mock_pm_class.return_value = mock_pm

    result = main(["uninstall", "--json", "x"])
    assert result == 0
    assert '"ok": true' in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_json_install(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.install_and_persist.return_value = _metadata("x")
    mock_pm_class.return_value = mock_pm

    result = main(["install", "--json", "/path"])
    assert result == 0
    assert '"name": "x"' in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_json_update(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.update = AsyncMock(return_value=[_metadata("x", "2.0")])
    mock_pm_class.return_value = mock_pm

    result = main(["update", "--json", "x"])
    assert result == 0
    assert '"version": "2.0"' in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_json_validate(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.validate.return_value = ["issue"]
    mock_pm_class.return_value = mock_pm

    result = main(["validate", "--json", "/path"])
    assert result == 1
    captured = capsys.readouterr()
    assert '"ok": false' in captured.out
    assert "issue" in captured.out


@patch("nova_harness.cli.package.PackageManager")
def test_json_info_none(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.info.return_value = None
    mock_pm_class.return_value = mock_pm

    result = main(["info", "--json", "x"])
    assert result == 0
    assert "null" in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_json_list_package_view(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.list_with_resources.return_value = {
        "b": PackageView(
            name="b", version="1", description="", agents=[], tools=[], skills=[]
        )
    }
    mock_pm_class.return_value = mock_pm

    result = main(["list", "--json"])
    assert result == 0
    assert '"b"' in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_error_with_json(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.list.side_effect = RuntimeError("boom")
    mock_pm_class.return_value = mock_pm

    result = main(["list", "--json", "--flat"])
    assert result == 1
    captured = capsys.readouterr()
    assert '"error": "boom"' in captured.out


@patch("nova_harness.cli.package.PackageManager")
def test_error_plain(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.list.side_effect = RuntimeError("boom")
    mock_pm_class.return_value = mock_pm

    result = main(["list", "--flat"])
    assert result == 1
    captured = capsys.readouterr()
    assert "Error: boom" in captured.err


def test_main_no_command_exits():
    with pytest.raises(SystemExit):
        main([])
