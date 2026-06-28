"""Tests for uninstall --remove-deps behavior."""

import os
import tempfile
from unittest.mock import patch

from nova_harness.core.package import PackageManager
from nova_harness.core.types.package_manager import PackageMetadata


def test_remove_only_unused_dependencies():
    with tempfile.TemporaryDirectory() as agent_dir:
        pm = PackageManager(agent_dir=agent_dir)

        # Manually register two packages: one uses "shared", the other uses "shared" and "unique"
        pm._add_to_manifest(
            PackageMetadata(
                name="pkg-a",
                version="1.0.0",
                description="",
                kind="bundle",
                source="",
                install_path="",
                installed_at="",
                installed_dependencies=["shared>=1.0"],
            )
        )
        pm._add_to_manifest(
            PackageMetadata(
                name="pkg-b",
                version="1.0.0",
                description="",
                kind="bundle",
                source="",
                install_path="",
                installed_at="",
                installed_dependencies=["shared>=2.0", "unique>=1.0"],
            )
        )

        with patch(
            "nova_harness.core.package.core.uninstall_dependencies"
        ) as mock_uninstall:
            pm.uninstall("pkg-b", kind="bundle", remove_deps=True)

        mock_uninstall.assert_called_once()
        args, _ = mock_uninstall.call_args
        removed = args[0]
        # "shared" is still used by pkg-a, only "unique" should be removed
        assert "unique" in removed
        assert "shared" not in removed
