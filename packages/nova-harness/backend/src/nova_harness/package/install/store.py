"""Install path computation, dist-info persistence, and package derivation.

Nova 的安装元数据采用 **dist-info 目录**（对齐 Python 生态 pip/uv 的
``*.dist-info/`` 风格）：sibling 于已安装副本，安装时机制写入、之后只读，
作为安装事实的权威快照。包含：

- ``direct_url.json``：PEP 610 格式的 source 记录（url + editable/ref）；
- ``package_name``：安装时判定的 Python 分发名（自安装边界快照）；
- ``installed_at``：安装时间（ISO 8601）。

dist-info 缺失时（旧安装），查询层回退到磁盘内容推导
（副本 manifest + symlink 判定）。
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from urllib.parse import unquote, urlparse

from nova_harness.core.config.defaults import NPM_PACKAGES_DIR_NAME
from nova_harness.core.types.package import PackageMetadata, PackageSource
from nova_harness.package.manifest import (
    is_installable_python_package,
    read_manifest,
    read_package_name,
    resolve_package_dependencies,
)
from nova_harness.package.source.spec import get_package_identity


def metadata_dedup_key(pkg: PackageMetadata, base_dir: str) -> str:
    """``PackageMetadata`` 的去重键。

    有 source 时用 package identity（settings 分支）；磁盘-only 的包
    （settings 条目丢失，source 为空）用 install_path——空 source 若走
    identity 会被解析成 install_dir 本身，产生伪包条目。
    """
    if pkg.source:
        return get_package_identity(pkg.source, base_dir=base_dir)
    return f"path:{pkg.install_path}"


def basename(path: str) -> str:
    """Return the basename of *path* after normalizing separators."""
    return os.path.basename(os.path.normpath(path))


def sanitize_name(name: str) -> str:
    """Sanitize a package name for use as a directory name.

    保留字母、数字、连字符与下划线；其余字符替换为下划线。
    这样 ``my-agent`` 与 ``my_agent`` 仍保持不同目录，避免过度归一化导致
    同名覆盖。
    """
    sanitized = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return sanitized or "unknown"


def looks_like_source(value: str) -> bool:
    """Return True when *value* is a package source spec rather than a name."""
    s = value.strip()
    if s.startswith(("path:", "git:", "http://", "https://")):
        return True
    if s.startswith(("/", "./", "~/", "../")):
        return True
    # 无协议前缀的裸名称：若当前工作目录下存在同名目录，视为本地 path source。
    return os.path.isdir(s)


def resolve_managed_path(root: Path, *parts: str) -> Path:
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


def install_path_for_source(
    source_obj: "PackageSource", pkg_name: str, path_root: Path, git_root: Path
) -> Path:
    """Return the standard install path for a resolved source."""
    if source_obj.type == "git":
        if not source_obj.host or not source_obj.repo_path:
            raise ValueError(f"Invalid git source: {source_obj.spec}")
        return resolve_managed_path(git_root, source_obj.host, source_obj.repo_path)
    if source_obj.type == "npm":
        if not source_obj.npm_name:
            raise ValueError(f"Invalid npm source: {source_obj.spec}")
        # npm 与 git 同目录族（resolver 缓存即安装态——npm 包不经 copy 物化）
        npm_root = git_root.parent / NPM_PACKAGES_DIR_NAME
        return resolve_managed_path(
            npm_root, source_obj.npm_name.replace("/", "__").lstrip("@")
        )
    if source_obj.type == "path":
        return resolve_managed_path(path_root, sanitize_name(pkg_name))
    raise ValueError(f"Unsupported source type: {source_obj.type}")


# ------------------------------------------------------------------
# dist-info：安装事实的权威快照（PEP 610 风格）
# ------------------------------------------------------------------

DIST_INFO_DIR_SUFFIX = ".dist-info"


@dataclass
class DistInfo:
    """从 ``*.dist-info/`` 读出的安装事实快照。"""

    source: str = ""
    editable: bool = False
    package_name: str = ""
    installed_at: str = ""


def dist_info_dir(install_path: str) -> Path:
    """返回 *install_path* 的 sibling dist-info 目录（``<name>.dist-info``）。

    放在副本**旁边**而非内部：editable 副本是 symlink（写入会污染原源），
    git 副本会被 ``git clean -fdx`` 清空——sibling 是两类源都安全的位置
    （与 pip 的 ``site-packages/<pkg>.dist-info/`` 同构）。
    """
    p = Path(install_path)
    return p.parent / (p.name + DIST_INFO_DIR_SUFFIX)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_direct_url(source_obj: "PackageSource", abs_src: str, editable: bool) -> dict:
    """按 PEP 610 构造 ``direct_url.json`` 内容。"""
    if source_obj.type == "path":
        return {
            "url": Path(abs_src).as_uri(),
            "dir_info": {"editable": bool(editable)},
        }
    # git 源
    info: dict = {
        "url": source_obj.remote_url or source_obj.spec,
        "vcs_info": {"vcs": "git"},
    }
    if source_obj.ref:
        info["vcs_info"]["requested_revision"] = source_obj.ref
    return info


def write_dist_info(
    install_path: str,
    source_obj: "PackageSource",
    abs_src: str,
    *,
    editable: bool,
    package_name: str,
) -> None:
    """安装时写入 dist-info（机制写入、只读追加语义）。

    ``package_name`` 为空（本次安装未做 Python 自安装）时删除旧快照中
    可能残留的 ``package_name`` 文件，避免卸载逻辑按过期快照去卸载一个
    本次从未安装的 Python 分发。
    """
    d = dist_info_dir(install_path)
    d.mkdir(parents=True, exist_ok=True)
    direct_url = build_direct_url(source_obj, abs_src, editable)
    (d / "direct_url.json").write_text(
        json.dumps(direct_url, indent=2), encoding="utf-8"
    )
    package_name_path = d / "package_name"
    if package_name:
        package_name_path.write_text(package_name + "\n", encoding="utf-8")
    elif package_name_path.exists():
        package_name_path.unlink()
    (d / "installed_at").write_text(_now_iso() + "\n", encoding="utf-8")


def _source_from_direct_url(direct_url: dict) -> str:
    """把 PEP 610 direct_url 回读为 Nova 的 source 字符串（用于 identity）。"""
    url = direct_url.get("url", "")
    if not url:
        return ""
    if url.startswith("file://"):
        return unquote(urlparse(url).path)
    vcs = direct_url.get("vcs_info") or {}
    ref = vcs.get("requested_revision")
    return f"{url}@{ref}" if ref else url


def read_dist_info(install_path: str) -> Optional[DistInfo]:
    """读取 dist-info；不存在（旧安装）返回 ``None``。"""
    d = dist_info_dir(install_path)
    if not d.is_dir():
        return None
    direct_url_path = d / "direct_url.json"
    try:
        direct_url = (
            json.loads(direct_url_path.read_text(encoding="utf-8"))
            if direct_url_path.exists()
            else {}
        )
    except Exception:
        direct_url = {}
    package_name_path = d / "package_name"
    installed_at_path = d / "installed_at"
    return DistInfo(
        source=_source_from_direct_url(direct_url),
        editable=bool((direct_url.get("dir_info") or {}).get("editable", False)),
        package_name=(
            package_name_path.read_text(encoding="utf-8").strip()
            if package_name_path.exists()
            else ""
        ),
        installed_at=(
            installed_at_path.read_text(encoding="utf-8").strip()
            if installed_at_path.exists()
            else ""
        ),
    )


# ------------------------------------------------------------------
# Installed package derivation（dist-info 权威 + 磁盘推导兜底）
# ------------------------------------------------------------------
def scan_installed_package_dirs(path_root: Path, git_root: Path) -> List[Path]:
    """扫描安装根目录，返回所有已安装包的副本目录。

    - path 源：``path_root/<name>/``（一层，可能是 editable symlink）；
    - git 源：``git_root/<host>/<repo>/``（两层，含 .git 的目录）。
    """
    results: List[Path] = []

    if path_root.exists():
        for entry in sorted(path_root.iterdir(), key=lambda p: p.name):
            # editable symlink（目录链接）与普通副本都算；broken symlink 跳过。
            # 排除 dist-info 目录（<name>.dist-info 是元数据，不是包）。
            if (
                entry.is_dir()
                and not entry.name.startswith(".")
                and not entry.name.endswith(DIST_INFO_DIR_SUFFIX)
            ):
                results.append(entry)

    if git_root.exists():
        for host_dir in sorted(git_root.iterdir(), key=lambda p: p.name):
            if not host_dir.is_dir() or host_dir.name.startswith("."):
                continue
            for repo_dir in sorted(host_dir.iterdir(), key=lambda p: p.name):
                if repo_dir.is_dir() and (repo_dir / ".git").exists():
                    results.append(repo_dir)

    return results


def derive_python_package_name(package_dir: str) -> str:
    """重算 *package_dir* 的 Python 分发名（未自安装时返回空串）。"""
    declared = read_package_name(package_dir)
    if declared and is_installable_python_package(package_dir):
        return declared
    return ""


def derive_package_metadata(
    install_dir: Path,
    *,
    source: str = "",
    editable: Optional[bool] = None,
) -> PackageMetadata:
    """构造已安装副本的 ``PackageMetadata``。

    **dist-info 权威**：source/editable/package_name/installed_at 取安装时
    写入的快照（防副本篡改漂移）；dist-info 缺失（旧安装）时回退推导——
    editable 按 ``install_dir.is_symlink()``、package_name 重算。
    name/version/description/author/dependencies 始终读副本 manifest
    （那是包内容的唯一事实，不进入 dist-info）。
    """
    install_dir_str = str(install_dir)
    manifest = read_manifest(install_dir_str)
    pkg_name = manifest.name or basename(install_dir_str)
    deps, _ = resolve_package_dependencies(install_dir_str)

    dist = read_dist_info(install_dir_str)
    if dist is not None:
        source = dist.source or source
        editable = dist.editable
        package_name = dist.package_name
        installed_at = dist.installed_at
    else:
        if editable is None:
            editable = install_dir.is_symlink()
        package_name = derive_python_package_name(install_dir_str)
        installed_at = ""

    return PackageMetadata(
        name=pkg_name,
        version=manifest.version,
        description=manifest.description,
        author=manifest.author,
        source=source,
        install_path=install_dir_str,
        editable=bool(editable),
        package_name=package_name,
        installed_at=installed_at,
        dependencies=deps,
        requires=list((manifest.nova.requires if manifest.nova else None) or []),
    )


__all__ = [
    "DIST_INFO_DIR_SUFFIX",
    "DistInfo",
    "basename",
    "build_direct_url",
    "derive_package_metadata",
    "derive_python_package_name",
    "dist_info_dir",
    "install_path_for_source",
    "looks_like_source",
    "metadata_dedup_key",
    "read_dist_info",
    "resolve_managed_path",
    "sanitize_name",
    "scan_installed_package_dirs",
    "write_dist_info",
]
