"""
nova-pkg CLI 单元测试。
"""

from unittest.mock import MagicMock, patch

import pytest

from nova_harness.cli.package import main
from nova_harness.core.types.package_manager import BundleView, PackageMetadata


def _metadata(name="pkg", kind="agent", version="1.0.0") -> PackageMetadata:
    return PackageMetadata(
        name=name,
        version=version,
        description="desc",
        kind=kind,
        source="/src",
        install_path="/path",
        installed_at="now",
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
    mock_pm.list.return_value = [_metadata("a", "agent"), _metadata("t", "tool")]
    mock_pm_class.return_value = mock_pm

    result = main(["list", "--flat"])
    assert result == 0
    captured = capsys.readouterr()
    assert "a" in captured.out
    assert "t" in captured.out


@patch("nova_harness.cli.package.PackageManager")
def test_list_bundle_view(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.list_by_bundle.return_value = {
        "my-bundle": BundleView(
            name="my-bundle",
            version="1.0",
            description="bundle desc",
            agents=[_metadata("a", "agent")],
            tools=[_metadata("t", "tool")],
            skills=[_metadata("s", "skill")],
        )
    }
    mock_pm_class.return_value = mock_pm

    result = main(["list"])
    assert result == 0
    captured = capsys.readouterr()
    assert "Bundle: my-bundle" in captured.out
    assert "bundle desc" in captured.out
    assert "Agents: a" in captured.out
    assert "Tools:  t" in captured.out


@patch("nova_harness.cli.package.PackageManager")
def test_list_bundle_view_empty(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.list_by_bundle.return_value = {}
    mock_pm_class.return_value = mock_pm

    result = main(["list"])
    assert result == 0
    assert "No packages installed" in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_install(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.install.return_value = _metadata("x", "agent")
    mock_pm_class.return_value = mock_pm

    result = main(["install", "/path/to/x", "--kind", "agent", "--name", "x"])
    assert result == 0
    mock_pm.install.assert_called_once_with(
        "/path/to/x", kind="agent", name="x", no_deps=False
    )
    captured = capsys.readouterr()
    assert "Installed 'x'" in captured.out


@patch("nova_harness.cli.package.PackageManager")
def test_uninstall_found(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.uninstall.return_value = True
    mock_pm_class.return_value = mock_pm

    result = main(["uninstall", "x", "--kind", "agent"])
    assert result == 0
    mock_pm.uninstall.assert_called_once_with("x", kind="agent")
    assert "Uninstalled" in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_uninstall_not_found(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.uninstall.return_value = False
    mock_pm_class.return_value = mock_pm

    result = main(["uninstall", "x", "--kind", "agent"])
    assert result == 1
    assert "not found" in capsys.readouterr().err


@patch("nova_harness.cli.package.PackageManager")
def test_update(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.update.return_value = _metadata("x", "agent", "2.0")
    mock_pm_class.return_value = mock_pm

    result = main(["update", "x", "--kind", "agent"])
    assert result == 0
    mock_pm.update.assert_called_once_with("x", kind="agent")
    assert "Updated 'x'" in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_info_found(mock_pm_class, capsys):
    meta = _metadata("x", "agent")
    meta.dependencies = ["requests>=2.0"]
    mock_pm = MagicMock()
    mock_pm.info.return_value = meta
    mock_pm_class.return_value = mock_pm

    result = main(["info", "x", "--kind", "agent"])
    assert result == 0
    captured = capsys.readouterr()
    assert "Name:        x" in captured.out
    assert "Dependencies: requests>=2.0" in captured.out


@patch("nova_harness.cli.package.PackageManager")
def test_info_not_found(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.info.return_value = None
    mock_pm_class.return_value = mock_pm

    result = main(["info", "x", "--kind", "agent"])
    assert result == 1
    assert "not found" in capsys.readouterr().err


@patch("nova_harness.cli.package.PackageManager")
def test_validate_ok(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.validate.return_value = []
    mock_pm_class.return_value = mock_pm

    result = main(["validate", "/path", "--kind", "agent"])
    assert result == 0
    mock_pm.validate.assert_called_once_with("/path", kind="agent")
    assert "is a valid package" in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_validate_failed(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.validate.return_value = ["missing foo"]
    mock_pm_class.return_value = mock_pm

    result = main(["validate", "/path", "--kind", "agent"])
    assert result == 1
    assert "Validation failed" in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_json_list_flat(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.list.return_value = [_metadata("a", "agent")]
    mock_pm_class.return_value = mock_pm

    result = main(["--json", "list", "--flat"])
    assert result == 0
    captured = capsys.readouterr()
    assert '"name": "a"' in captured.out


@patch("nova_harness.cli.package.PackageManager")
def test_json_uninstall(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.uninstall.return_value = True
    mock_pm_class.return_value = mock_pm

    result = main(["--json", "uninstall", "x", "--kind", "agent"])
    assert result == 0
    assert '"ok": true' in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_json_install(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.install.return_value = _metadata("x", "agent")
    mock_pm_class.return_value = mock_pm

    result = main(["--json", "install", "/path", "--kind", "agent"])
    assert result == 0
    assert '"name": "x"' in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_json_update(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.update.return_value = _metadata("x", "agent", "2.0")
    mock_pm_class.return_value = mock_pm

    result = main(["--json", "update", "x", "--kind", "agent"])
    assert result == 0
    assert '"version": "2.0"' in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_json_validate(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.validate.return_value = ["issue"]
    mock_pm_class.return_value = mock_pm

    result = main(["--json", "validate", "/path", "--kind", "agent"])
    assert result == 1
    captured = capsys.readouterr()
    assert '"ok": false' in captured.out
    assert "issue" in captured.out


@patch("nova_harness.cli.package.PackageManager")
def test_json_info_none(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.info.return_value = None
    mock_pm_class.return_value = mock_pm

    result = main(["--json", "info", "x", "--kind", "agent"])
    assert result == 0
    assert "null" in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_json_list_bundle_view(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.list_by_bundle.return_value = {
        "b": BundleView(
            name="b", version="1", description="", agents=[], tools=[], skills=[]
        )
    }
    mock_pm_class.return_value = mock_pm

    result = main(["--json", "list"])
    assert result == 0
    assert '"b"' in capsys.readouterr().out


@patch("nova_harness.cli.package.PackageManager")
def test_error_with_json(mock_pm_class, capsys):
    mock_pm = MagicMock()
    mock_pm.list.side_effect = RuntimeError("boom")
    mock_pm_class.return_value = mock_pm

    result = main(["--json", "list", "--flat"])
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
