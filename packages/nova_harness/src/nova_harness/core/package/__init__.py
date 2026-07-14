"""Package manager for Nova agent configs, tools, skills, extensions, and packages.

Install, uninstall, list, update, and validate packages from local paths or git::

    >>> from nova_harness.core.package import PackageManager
    >>> pm = PackageManager()
    >>>
    >>> # Install a bundle and persist it to settings
    >>> pm.install_and_persist("/path/to/nova_coding_agent")
    >>>
    >>> # Resolve all runtime resources
    >>> paths = await pm.resolve_resources()
    >>>
    >>> pm.uninstall("nova-coding-agent")

The public facade is `PackageManager`. Lower-level building blocks are also
exported for advanced use cases:

- `PackageInstaller`: install / uninstall / list / info / validate
- `PackageResolver`: runtime resource path resolution
"""

from nova_harness.core.package.installer import PackageInstaller
from nova_harness.core.package.manager import (
    PackageInstallError,
    PackageManager,
    PackageUpdateError,
)
from nova_harness.core.package.resolver import PackageResolver

__all__ = [
    "PackageInstallError",
    "PackageManager",
    "PackageInstaller",
    "PackageResolver",
    "PackageUpdateError",
]
