"""Pure helper functions for package metadata persistence and install paths."""

import os
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from nova_harness.core.package.source import get_package_identity

if TYPE_CHECKING:
    from nova_harness.core.types.package_manager import PackageMetadata, PackageSource

NOVA_PACKAGE_METADATA_SUFFIX = ".nova-package.json"


def _basename(path: str) -> str:
    """Return the basename of *path* after normalizing separators."""
    return os.path.basename(os.path.normpath(path))


def _sanitize_name(name: str) -> str:
    """Sanitize a package name for use as a directory name.

    保留字母、数字、连字符与下划线；其余字符替换为下划线。
    这样 ``my-agent`` 与 ``my_agent`` 仍保持不同目录，避免过度归一化导致
    同名覆盖。
    """
    sanitized = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return sanitized or "unknown"


# ------------------------------------------------------------------
# Package metadata persistence (.nova-package.json)
# ------------------------------------------------------------------
def _metadata_file_path(install_path: str) -> Path:
    """Return the path to the Nova package metadata file next to *install_path*.

    Metadata is stored as a sibling file (``<install_dir>.nova-package.json``)
    rather than inside the install directory. This keeps Nova-managed metadata
    separate from package contents, so:

    - Git packages are not affected by ``git clean -fdx`` inside the clone.
    - Editable installs do not pollute the original source directory.
    - Uninstalling a package removes both the install directory and its metadata.
    """
    install_path_obj = Path(install_path)
    return install_path_obj.parent / (
        install_path_obj.name + NOVA_PACKAGE_METADATA_SUFFIX
    )


def _write_package_metadata(
    install_path: str,
    metadata: "PackageMetadata",
) -> None:
    """Persist package metadata next to *install_path*."""
    path = _metadata_file_path(install_path)
    path.write_text(metadata.model_dump_json(indent=2), encoding="utf-8")


def _read_package_metadata(install_path: str) -> Optional["PackageMetadata"]:
    """Read package metadata from the sibling ``.nova-package.json`` file."""
    path = _metadata_file_path(install_path)
    if not path.is_file():
        return None
    try:
        from nova_harness.core.types.package_manager import PackageMetadata

        return PackageMetadata.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _is_valid_install_path(install_path: Path) -> bool:
    """Return True if the installed package path still exists.

    A broken editable symlink (target removed) is treated as non-existent so
    that the package is considered uninstalled.
    """
    return install_path.exists()


def _scan_installed_metadata(path_root: Path) -> List["PackageMetadata"]:
    """Scan *path_root* recursively for installed package metadata files."""
    results: List["PackageMetadata"] = []
    if not path_root.exists():
        return results
    for path in path_root.rglob(f"*{NOVA_PACKAGE_METADATA_SUFFIX}"):
        if not path.is_file():
            continue
        # Derive the sibling install directory from the metadata filename.
        suffix = NOVA_PACKAGE_METADATA_SUFFIX
        if not path.name.endswith(suffix):
            continue
        install_path = path.parent / path.name[: -len(suffix)]
        if not _is_valid_install_path(install_path):
            continue
        meta = _read_package_metadata(str(install_path))
        if meta is not None:
            results.append(meta)
    return results


def _find_installed_metadata_by_source(
    path_root: Path, source: str, base_dir: Optional[str] = None
) -> Optional["PackageMetadata"]:
    """Find an installed path package by its recorded source spec.

    Comparison is done via ``get_package_identity`` so that symbolic-link
    differences (e.g. ``/tmp`` vs ``/private/tmp`` on macOS) do not break
    matching. The optional *base_dir* stabilizes relative path identity.
    """
    target_identity = get_package_identity(source, base_dir)
    for meta in _scan_installed_metadata(path_root):
        if get_package_identity(meta.source, base_dir) == target_identity:
            return meta
    return None


def _find_installed_metadata_by_name(
    path_root: Path, name: str, git_root: Optional[Path] = None
) -> Optional["PackageMetadata"]:
    """Find an installed package by its display name across path and git roots."""
    for root in (path_root, git_root):
        if root is None:
            continue
        for meta in _scan_installed_metadata(root):
            if meta.name == name:
                return meta
    return None


def _is_skill_path(path: str) -> bool:
    """Check whether *path* is a SKILL.md file or a directory containing one."""
    if os.path.isfile(path) and os.path.basename(path) == "SKILL.md":
        return True
    if os.path.isdir(path):
        return os.path.exists(os.path.join(path, "SKILL.md"))
    return False


def _looks_like_source(value: str) -> bool:
    """Return True when *value* is a package source spec rather than a name."""
    s = value.strip()
    if s.startswith(("path:", "git:", "http://", "https://")):
        return True
    if s.startswith(("/", "./", "~/", "../")):
        return True
    # 无协议前缀的裸名称：若当前工作目录下存在同名目录，视为本地 path source。
    return os.path.isdir(s)


def _resolve_managed_path(root: Path, *parts: str) -> Path:
    """Resolve *parts* under *root* and reject path traversal.

    确保最终路径不逃出 *root*，防止畸形 source 将包安装到 Nova 管理目录之外。

    注意：使用 ``os.path.normpath(os.path.abspath(...))`` 规范化 ``..``，但不像
    ``Path.resolve()`` 那样跟随符号链接，避免 editable 安装的 symlink 被解析到
    原源目录后误判为逃逸。
    """
    resolved_root = root.resolve()
    raw_path = resolved_root / "/".join(parts)
    # normpath + abspath 去掉 .. 但不跟随 symlink
    resolved_path = Path(os.path.normpath(os.path.abspath(str(raw_path))))
    if resolved_path != resolved_root and not str(resolved_path).startswith(
        f"{resolved_root}{os.sep}"
    ):
        raise ValueError(
            f"Refusing to use path outside package install root: {resolved_path}"
        )
    return resolved_path


def _install_path_for_source(
    source_obj: "PackageSource", pkg_name: str, path_root: Path, git_root: Path
) -> Path:
    """Return the standard install path for a resolved source."""
    if source_obj.type == "git":
        if not source_obj.host or not source_obj.repo_path:
            raise ValueError(f"Invalid git source: {source_obj.spec}")
        return _resolve_managed_path(git_root, source_obj.host, source_obj.repo_path)
    if source_obj.type == "path":
        return _resolve_managed_path(path_root, _sanitize_name(pkg_name))
    raise ValueError(f"Unsupported source type: {source_obj.type}")


__all__ = [
    "NOVA_PACKAGE_METADATA_SUFFIX",
    "_basename",
    "_find_installed_metadata_by_name",
    "_find_installed_metadata_by_source",
    "_install_path_for_source",
    "_is_skill_path",
    "_is_valid_install_path",
    "_looks_like_source",
    "_read_package_metadata",
    "_sanitize_name",
    "_scan_installed_metadata",
    "_write_package_metadata",
]
