"""Package manager for Nova agent configs, tools, and bundles.

Install, uninstall, list, update, and validate packages from local paths or git::

    >>> from nova_harness.core.package import PackageManager
    >>> pm = PackageManager()
    >>>
    >>> # Install a single agent config
    >>> pm.install("/path/to/my_agent", kind="agent")
    >>>
    >>> # Install a bundle (agents + tools)
    >>> pm.install("git:github.com/liujinming/nova-coding-agent@v1.0.0")
    >>>
    >>> for pkg in pm.list():
    ...     print(f"{pkg.name} ({pkg.kind}) @ {pkg.version} from {pkg.source}")
    >>>
    >>> pm.uninstall("my_agent", kind="agent")
"""

from nova_harness.core.package.core import PackageManager

__all__ = ["PackageManager"]
