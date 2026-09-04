"""Package source specification parsing and collection.

Supported source specs::

    path:/absolute/path
    path:./relative/path
    /absolute/path                (implicit path)
    ./relative/path               (implicit path)
    git:github.com/user/repo@ref
    git:git@github.com:user/repo.git@ref
    https://github.com/user/repo
    https://github.com/user/repo@ref

Editable is no longer expressed as a source prefix. Use the ``--editable``
CLI flag or ``editable: true`` in a dict-style settings entry instead.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from nova_harness.core.package.source._semver import parse_version_spec
from nova_harness.core.types.config.settings import PackageSourceSpec
from nova_harness.core.types.package import PackageFilter, PackageSource

# npm dist-tag 名的基本形态校验（npm 约定 tag 不与 semver 冲突；
# 解析期只做语法 sanity——存在性由 resolver 查询 registry 时校验）。
_NPM_DIST_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def parse_source(spec: str) -> PackageSource:
    """Parse a source specification into a PackageSource."""
    spec = spec.strip()

    if spec.startswith("editable:"):
        raise ValueError(
            "The 'editable:' source prefix is no longer supported. "
            "Use the --editable CLI flag or set editable: true in settings."
        )

    if spec.startswith("path:"):
        return PackageSource(type="path", spec=spec, path=spec[5:].strip())

    if spec.startswith("git:"):
        return _parse_git_spec(spec[4:].strip(), original=spec)

    if spec.startswith("npm:"):
        return _parse_npm_spec(spec[4:].strip(), original=spec)

    if spec.startswith(("http://", "https://")):
        return _parse_git_spec(spec, original=spec)

    # Default to path source.
    return PackageSource(type="path", spec=spec, path=spec)


def _parse_npm_spec(rest: str, original: str) -> PackageSource:
    """解析 npm 源：``npm:<name>[@<version>]``（``@scope/name@version`` 形态兼容）。

    版本段取**最后一个** ``@`` 作分隔（scope 前缀自带一个 ``@``）。
    版本支持精确版本（``1.2.3``，可带 prerelease/build 与 ``v`` 前缀）、
    ``^``/``~`` range、裸部分版本与段级通配（``1`` / ``1.2`` / ``1.2.x``）、
    比较器集（``>=1.2.0 <2.0.0``）、``||`` 并集、hyphen range
    （``1.2.3 - 2.3.4``）与 ``*``/``x``/``X``（任意版本）；``latest`` 与
    省略等价（归一为 ``None``）。解析期只做语法校验、**不触网**——range
    求值（max satisfying）发生在 resolver 查询 registry 之后。
    """
    if not rest:
        raise ValueError(f"Invalid npm source (empty package name): {original}")
    name, _, version = rest.rpartition("@")
    if not name:  # 无 @ 分隔：整个 rest 是包名
        name, version = rest, ""
    if not name.startswith("@") and "/" in name:
        raise ValueError(f"Invalid npm package name: {name}")
    if version == "latest":
        version = ""  # latest 归一为 None
    if version:
        try:
            parse_version_spec(version)
        except ValueError:
            # 非版本语法的字符串按 dist-tag 接受（beta/next/canary——npm
            # 约定 tag 名不与 semver 冲突，能进这个分支即说明不是合法
            # semver；解析期不触网，tag 是否存在由 resolver 查询
            # registry 时校验并报出可用 tag 清单）。
            if not _NPM_DIST_TAG_RE.match(version):
                raise ValueError(
                    f"Invalid npm version spec or dist-tag: {original}"
                ) from None
    return PackageSource(
        type="npm",
        spec=original,
        npm_name=name,
        npm_version=version or None,
    )


def _parse_git_spec(rest: str, original: str) -> PackageSource:
    """Parse the git-specific portion of a source spec.

    在确定 host/path 后，取 path 部分的**第一个** ``@`` 作为 ref 分隔符，
    从而支持 ``feature/foo`` 等含 ``/`` 的 ref。
    """
    # SCP-like: git@github.com:user/repo@ref
    scp_match = re.match(r"git@([^:]+):(.+)$", rest)
    if scp_match:
        host = scp_match.group(1)
        path_with_maybe_ref = scp_match.group(2)
        repo_path, ref = _split_git_path_and_ref(path_with_maybe_ref)
        if not repo_path:
            raise ValueError(f"Invalid git source: {original}")
        normalized_path = _normalize_repo_path(repo_path)
        remote_url = f"git@{host}:{normalized_path}"
        return PackageSource(
            type="git",
            spec=original,
            remote_url=remote_url,
            host=host,
            repo_path=normalized_path,
            ref=ref,
        )

    # Full URL with scheme.
    parsed = urlparse(rest)
    if parsed.scheme and parsed.netloc:
        host = parsed.netloc
        path_with_maybe_ref = parsed.path.lstrip("/")
        repo_path, ref = _split_git_path_and_ref(path_with_maybe_ref)
        if not repo_path:
            raise ValueError(f"Invalid git source: {original}")
        normalized_path = _normalize_repo_path(repo_path)
        parsed = parsed._replace(path=f"/{normalized_path}")
        remote_url = parsed.geturl()
        return PackageSource(
            type="git",
            spec=original,
            remote_url=remote_url,
            host=host,
            repo_path=normalized_path,
            ref=ref,
        )

    # Plain host/path shorthand: github.com/user/repo@ref
    slash_index = rest.find("/")
    if slash_index < 0:
        raise ValueError(f"Invalid git source: {original}")
    host = rest[:slash_index]
    path_with_maybe_ref = rest[slash_index + 1 :]
    repo_path, ref = _split_git_path_and_ref(path_with_maybe_ref)
    if not repo_path:
        raise ValueError(f"Invalid git source: {original}")
    normalized_path = _normalize_repo_path(repo_path)
    return PackageSource(
        type="git",
        spec=original,
        remote_url=f"https://{host}/{normalized_path}",
        host=host,
        repo_path=normalized_path,
        ref=ref,
    )


def _split_git_path_and_ref(path_with_maybe_ref: str) -> Tuple[str, Optional[str]]:
    """在 path 部分中按第一个 ``@`` 切分出 repo path 与 ref。

    返回 ``(repo_path, ref)``；若没有 ref 或切分后任一半为空，则 ref 为 ``None``。
    """
    at_index = path_with_maybe_ref.find("@")
    if at_index < 0:
        return path_with_maybe_ref, None
    repo_path = path_with_maybe_ref[:at_index]
    ref = path_with_maybe_ref[at_index + 1 :]
    if not repo_path or not ref:
        return path_with_maybe_ref, None
    return repo_path, ref


def _normalize_repo_path(repo_path: str) -> str:
    """Strip .git suffix and leading/trailing slashes from a repo path."""
    repo_path = repo_path.strip("/")
    if repo_path.endswith(".git"):
        repo_path = repo_path[:-4]
    return repo_path


def get_package_source_string(spec: PackageSourceSpec) -> str:
    """从 package source spec 中提取 source 字符串。"""
    if isinstance(spec, str):
        return spec
    if isinstance(spec, dict):
        source = spec.get("source")
        if isinstance(source, str):
            return source
    raise ValueError(f"Invalid package source spec: {spec}")


def parse_package_source_spec(
    spec: PackageSourceSpec,
) -> Tuple[str, bool, PackageFilter]:
    """从 package source spec 中提取 source 字符串、editable 标志与资源过滤器。"""
    if isinstance(spec, str):
        return spec, False, PackageFilter()

    if isinstance(spec, dict):
        source = spec.get("source")
        if not isinstance(source, str):
            raise ValueError(
                f"Package source spec must have a string 'source' field: {spec}"
            )
        editable = bool(spec.get("editable", False))
        filter_obj = PackageFilter(
            extensions=spec.get("extensions"),
            skills=spec.get("skills"),
            prompts=spec.get("prompts"),
            tools=spec.get("tools"),
            agents=spec.get("agents"),
            personas=spec.get("personas"),
            autoload=spec.get("autoload"),
        )
        return source, editable, filter_obj

    raise ValueError(f"Invalid package source spec: {spec}")


def merge_package_source_specs(
    base: PackageSourceSpec,
    override: PackageSourceSpec,
) -> PackageSourceSpec:
    """合并两个 package source spec，保留 base 中的 filters 与 editable。

    override 中的同名字段会覆盖 base；若 override 是字符串或缺少 editable/filters，
    则从 base 补齐，避免重复安装时丢失用户配置的过滤器和 editable 标志。
    """
    from typing import Any

    base_source, base_editable, base_filter = parse_package_source_spec(base)
    override_source, override_editable, override_filter = parse_package_source_spec(
        override
    )

    merged: dict[str, Any] = {"source": override_source}

    editable = (
        override_editable
        if isinstance(override, dict) and "editable" in override
        else base_editable
    )
    if editable:
        merged["editable"] = True

    def _pick(name: str) -> Optional[List[str]]:
        override_value = getattr(override_filter, name)
        if isinstance(override, dict) and name in override:
            return override_value
        return getattr(base_filter, name)

    for field in (
        "extensions",
        "skills",
        "prompts",
        "tools",
        "agents",
    ):
        value = _pick(field)
        if value is not None:
            merged[field] = value

    # autoload 是布尔开关而非列表，单独按同名规则合并。
    autoload = (
        override_filter.autoload
        if isinstance(override, dict) and "autoload" in override
        else base_filter.autoload
    )
    if autoload is not None:
        merged["autoload"] = autoload

    # 没有任何额外字段时退化为字符串，保持 settings 简洁。
    if len(merged) == 1:
        return merged["source"]
    return merged


def normalize_package_source_for_settings(
    spec: PackageSourceSpec, base_dir: str, cwd: Optional[str] = None
) -> PackageSourceSpec:
    """把 package source spec 中的本地路径相对化到 *base_dir*。

    只对 ``path:`` 源做相对化；git/https 源原样返回（但保留 dict 中的 filters
    与 editable 字段）。相对路径按 *cwd* 解析（未提供时使用进程当前工作目录），
    再用 realpath 消除符号链接差异，最后相对化到 *base_dir*。
    """
    source_str, _, filters = parse_package_source_spec(spec)
    source_obj = parse_source(source_str)

    if source_obj.type != "path":
        # 非 path 源不需要修改 source，但如果是 dict 仍需保留 filters/editable。
        if isinstance(spec, dict):
            return dict(spec)
        return spec

    path = source_obj.path or ""
    # 相对路径按 cwd 解析（未提供时回退到进程 CWD），绝对路径保持不变。
    resolve_cwd = cwd if cwd is not None else os.getcwd()
    abs_path = os.path.realpath(
        os.path.join(
            os.path.realpath(os.path.expanduser(resolve_cwd)), os.path.expanduser(path)
        )
        if not os.path.isabs(os.path.expanduser(path))
        else os.path.expanduser(path)
    )
    base = os.path.realpath(os.path.expanduser(base_dir))
    rel = os.path.relpath(abs_path, base)

    new_source = f"path:{rel}"
    if isinstance(spec, str):
        return new_source

    new_spec = dict(spec)
    new_spec["source"] = new_source
    return new_spec


def resolve_package_source_from_settings(
    spec: PackageSourceSpec, base_dir: str
) -> PackageSourceSpec:
    """把 settings 中相对化的本地路径解析为绝对路径。

    如果路径已经是绝对路径，则原样返回，保证向后兼容。
    """
    source_str, _, filters = parse_package_source_spec(spec)
    source_obj = parse_source(source_str)
    if source_obj.type != "path":
        if isinstance(spec, dict):
            return dict(spec)
        return spec

    path = source_obj.path or ""
    if os.path.isabs(path):
        if isinstance(spec, dict):
            return dict(spec)
        return spec

    base = os.path.realpath(os.path.expanduser(base_dir))
    abs_path = os.path.normpath(os.path.join(base, path))
    new_source = f"path:{abs_path}"
    if isinstance(spec, str):
        return new_source

    new_spec = dict(spec)
    new_spec["source"] = new_source
    return new_spec


def get_package_identity(
    source: PackageSourceSpec, base_dir: Optional[str] = None
) -> str:
    """Return a canonical identity key for *source*.

    The identity is used to deduplicate and match installed packages. Two
    source specs that point to the same package identity are considered the
    same install target, even if they use different refs or path expressions.

    For local path sources, *base_dir* controls how relative paths are
    resolved. When *base_dir* is provided, the path is resolved relative to
    *base_dir* (matching a specific scope). When omitted, it is resolved
    relative to the current working directory (for user input).

    The ``editable`` flag is intentionally ignored for identity purposes: a
    path source installed normally and the same path installed editable share
    the same identity.

    Examples::

        git:github.com/user/repo@main      -> git:github.com/user/repo
        git:github.com/user/repo@v1.0      -> git:github.com/user/repo
        path:./my-agent (base_dir=/a/b)    -> local:/a/b/my-agent
    """
    source_str = get_package_source_string(source)
    source_obj = parse_source(source_str)
    if source_obj.type == "git":
        if not source_obj.host or not source_obj.repo_path:
            return source_str
        return f"git:{source_obj.host}/{source_obj.repo_path}"
    if source_obj.type == "path":
        path = source_obj.path or ""
        if base_dir is not None:
            abs_path = os.path.realpath(
                os.path.join(os.path.realpath(os.path.expanduser(base_dir)), path)
            )
        else:
            abs_path = os.path.realpath(os.path.expanduser(path))
        return f"local:{abs_path}"
    return source_str


@dataclass(frozen=True)
class ResolvedScopedSources:
    """跨 scope 去重后的 package source 列表。

    project scope 对同一 identity 的 source 优先于 user scope。
    """

    user: List[PackageSourceSpec] = field(default_factory=list)
    project: List[PackageSourceSpec] = field(default_factory=list)


class PackageSourceCollection:
    """管理一组跨 scope 的 package sources。

    负责把原始 settings spec 解析为可消费的 source 字符串，
    计算 package identity，并按 project-priority 去重。
    """

    def __init__(
        self,
        user_base_dir: str,
        project_base_dir: str,
    ) -> None:
        self._user_base_dir = user_base_dir
        self._project_base_dir = project_base_dir

    def resolve(
        self,
        user_sources: List[PackageSourceSpec],
        project_sources: List[PackageSourceSpec],
    ) -> ResolvedScopedSources:
        """返回跨 scope 去重后的 source 列表。

        同一 identity 的 source 同时在 project 和 user 中存在时，
        仅保留 project scope 的条目——**例外**：project 条目为
        ``autoload: false`` 时它是 user 条目的 delta（局部翻转而非
        独立覆盖），两个条目都保留，由解析层先应用 delta 再应用 user 底。
        """
        project_identities: Dict[str, PackageSourceSpec] = {}
        project_autoload_false: Set[str] = set()
        deduped_project: List[PackageSourceSpec] = []

        for spec in project_sources:
            source = get_package_source_string(spec)
            if not source:
                continue
            identity = get_package_identity(source, base_dir=self._project_base_dir)
            project_identities[identity] = spec
            _, _, project_filter = parse_package_source_spec(spec)
            if project_filter.autoload is False:
                project_autoload_false.add(identity)
            deduped_project.append(spec)

        deduped_user: List[PackageSourceSpec] = []
        seen_user_identities: Set[str] = set()

        for spec in user_sources:
            source = get_package_source_string(spec)
            if not source:
                continue
            identity = get_package_identity(source, base_dir=self._user_base_dir)
            if (
                identity in project_identities
                and identity not in project_autoload_false
            ):
                continue
            if identity in seen_user_identities:
                continue
            seen_user_identities.add(identity)
            deduped_user.append(spec)

        return ResolvedScopedSources(
            user=deduped_user,
            project=deduped_project,
        )


__all__ = [
    "PackageSource",
    "ResolvedScopedSources",
    "PackageSourceCollection",
    "get_package_identity",
    "get_package_source_string",
    "merge_package_source_specs",
    "normalize_package_source_for_settings",
    "parse_package_source_spec",
    "parse_source",
    "resolve_package_source_from_settings",
]
