"""Skill 加载实现。

包含两个层次：

1. 文件级：发现 ``SKILL.md`` 文件并解析其 YAML frontmatter。
2. Resource 级：由 ``PackageResolver`` 提供路径，处理去重与冲突诊断。
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from nova_harness.core.package.utils.ignore import (
    IgnoreSpecWithPrefix,
    is_ignored_by_specs,
    load_ignore_specs,
)
from nova_harness.core.resources.source_info import (
    find_source_info_for_path,
    source_info_from_metadata,
)
from nova_harness.core.types.extensions import SourceInfo
from nova_harness.core.types.package_manager import (
    ResolvedResource,
    SourceOrigin,
    SourceScope,
)
from nova_harness.core.types.resources.diagnostics import (
    ResourceCollision,
    ResourceDiagnostic,
)
from nova_harness.core.types.skills import Skill
from nova_harness.core.utils.files import canonicalize_path
from nova_harness.core.utils.frontmatter import parse_frontmatter

_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")
_MAX_NAME_LEN = 64
_MAX_DESCRIPTION_LEN = 1024


def validate_name(name: str) -> Tuple[bool, str]:
    """校验 skill 名称是否合法（与 TS 规则对齐）。"""
    if not name:
        return False, "Skill name is required"
    if len(name) > _MAX_NAME_LEN:
        return False, f"Skill name exceeds {_MAX_NAME_LEN} characters"
    if not _NAME_PATTERN.match(name):
        return (
            False,
            "Skill name must contain lowercase a-z, 0-9, hyphens only",
        )
    if name.startswith("-") or name.endswith("-"):
        return False, "Skill name must not start or end with a hyphen"
    if "--" in name:
        return False, "Skill name must not contain consecutive hyphens"
    return True, ""


def validate_description(description: str) -> Tuple[bool, str]:
    """校验 skill 描述是否合法。"""
    if not description:
        return False, "Skill description is required"
    if len(description) > _MAX_DESCRIPTION_LEN:
        return (
            False,
            f"Skill description exceeds {_MAX_DESCRIPTION_LEN} characters",
        )
    return True, ""


def load_skill_from_file(
    file_path: str,
    source_label: str = "unknown",
    source_info: Optional[SourceInfo] = None,
) -> Optional[Skill]:
    """加载单个 SKILL.md 文件。

    如果 frontmatter 不合法或缺少必要字段，返回 None。
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (IOError, UnicodeDecodeError):
        return None

    parsed = parse_frontmatter(content)
    fm = parsed.frontmatter

    raw_name = fm.get("name") if isinstance(fm, dict) else None
    description = fm.get("description") if isinstance(fm, dict) else None
    disable = (
        bool(fm.get("disable-model-invocation")) if isinstance(fm, dict) else False
    )

    # frontmatter 未提供有效 name 时，fallback 到父目录名（与 TS 行为对齐）。
    parent_dir_name = Path(file_path).parent.name
    if isinstance(raw_name, str) and raw_name.strip():
        name = raw_name.strip()
    else:
        name = parent_dir_name

    if not isinstance(description, str):
        return None

    ok, _ = validate_name(name)
    if not ok:
        return None
    ok, _ = validate_description(description)
    if not ok:
        return None

    return Skill(
        name=name,
        description=description,
        file_path=file_path,
        base_dir=str(Path(file_path).parent),
        disable_model_invocation=disable,
        source_label=source_label,
        source_info=source_info,
    )


def load_skills_from_dir(
    directory: str,
    source_label: str = "unknown",
    source_info: Optional[SourceInfo] = None,
    allowed_names: Optional[set[str]] = None,
) -> List[Skill]:
    """递归扫描目录，加载所有 ``SKILL.md``，应用目录树中的 ignore 规则。

    如果某个目录包含 ``SKILL.md``，则停止继续递归该目录的子目录。

    Args:
        allowed_names: 若提供，仅返回名称在该集合中的 skill。
    """
    root_dir = str(Path(directory).resolve())
    specs = load_ignore_specs(root_dir)
    return _load_skills_from_dir_internal(
        directory,
        source_label=source_label,
        source_info=source_info,
        allowed_names=allowed_names,
        root_dir=root_dir,
        specs=specs,
    )


def _load_skills_from_dir_internal(
    directory: str,
    source_label: str = "unknown",
    source_info: Optional[SourceInfo] = None,
    allowed_names: Optional[set[str]] = None,
    root_dir: Optional[str] = None,
    specs: Optional[List[IgnoreSpecWithPrefix]] = None,
) -> List[Skill]:
    skills: List[Skill] = []
    root = Path(directory)
    if not root.exists() or not root.is_dir():
        return skills

    resolved_root = root.resolve()
    if root_dir is None:
        root_dir = str(resolved_root)
        specs = load_ignore_specs(root_dir)
    assert specs is not None

    try:
        rel_prefix = str(resolved_root.relative_to(Path(root_dir).resolve()))
    except ValueError:
        rel_prefix = ""
    if rel_prefix:
        rel_prefix += "/"

    for entry in sorted(root.iterdir()):
        entry_rel = f"{rel_prefix}{entry.name}"

        if entry.is_file() and entry.name == "SKILL.md":
            if is_ignored_by_specs(entry_rel, is_dir=False, specs=specs):
                continue
            skill = load_skill_from_file(
                str(entry), source_label=source_label, source_info=source_info
            )
            if skill is not None and (
                allowed_names is None or skill.name in allowed_names
            ):
                skills.append(skill)
        elif entry.is_dir():
            if is_ignored_by_specs(entry_rel, is_dir=True, specs=specs):
                continue
            skill_file = entry / "SKILL.md"
            if skill_file.exists():
                skill_rel = f"{entry_rel}/SKILL.md"
                if is_ignored_by_specs(skill_rel, is_dir=False, specs=specs):
                    continue
                skill = load_skill_from_file(
                    str(skill_file),
                    source_label=source_label,
                    source_info=source_info,
                )
                if skill is not None and (
                    allowed_names is None or skill.name in allowed_names
                ):
                    skills.append(skill)
            else:
                skills.extend(
                    _load_skills_from_dir_internal(
                        str(entry),
                        source_label=source_label,
                        source_info=source_info,
                        allowed_names=allowed_names,
                        root_dir=root_dir,
                        specs=specs,
                    )
                )

    return skills


# =============================================================================
# Resource 级加载
# =============================================================================


def _source_label_from_resource(resource: ResolvedResource) -> str:
    """根据 resolver 元数据生成 skill 来源标签。"""
    metadata = resource.metadata
    if metadata.origin == SourceOrigin.PACKAGE:
        return "package"
    scope = metadata.scope
    if isinstance(scope, SourceScope):
        return scope.value
    return str(scope)


def _collect_skill_paths(
    additional_paths: Optional[List[str]],
    no_skills: bool,
    resolved_resources: Optional[List[ResolvedResource]] = None,
    extension_source_infos: Optional[List[SourceInfo]] = None,
) -> List[Tuple[str, str, Optional[SourceInfo]]]:
    """Return list of (path, source_label, source_info) to load skills from.

    ``no_skills=True`` 只禁用 ``resolved_resources``（自动发现/包解析的资源），
    不禁用 ``additional_paths``（CLI/程序显式传入的路径），与 TS 行为一致。
    """
    paths: List[Tuple[str, str, Optional[SourceInfo]]] = []
    seen: set = set()

    def add(path: str, label: str, source_info: Optional[SourceInfo] = None) -> None:
        resolved = Path(path).resolve()
        if not resolved.exists():
            return
        real = canonicalize_path(str(resolved))
        if real not in seen:
            seen.add(real)
            paths.append((str(resolved), label, source_info))

    if not no_skills:
        for resource in resolved_resources or []:
            if not resource.enabled:
                continue
            add(
                resource.path,
                _source_label_from_resource(resource),
                source_info_from_metadata(resource),
            )

    for p in additional_paths or []:
        resolved = Path(p).resolve()
        source_info = None
        if extension_source_infos:
            source_info = find_source_info_for_path(
                str(resolved), extension_source_infos
            )
        add(p, "path", source_info)

    return paths


def load_skills(
    additional_paths: Optional[List[str]] = None,
    no_skills: bool = False,
    resolved_resources: Optional[List[ResolvedResource]] = None,
    extension_source_infos: Optional[List[SourceInfo]] = None,
    allowed_names: Optional[set[str]] = None,
) -> Tuple[Dict[str, Skill], List[ResourceDiagnostic]]:
    """加载所有可用 skill。

    返回 ``(skills_by_name, diagnostics)``。同名 skill 按优先级保留第一个，
    后续重复项生成 collision 诊断。

    路径必须由 ``PackageResolver`` 提供；``additional_paths`` 作为补充追加。

    Args:
        allowed_names: 若提供，仅加载名称在该集合中的 skill。
            注意：白名单过滤在去重**之前**执行，被过滤掉的 skill 不会产生
            collision 诊断。
    """
    candidates = _collect_skill_paths(
        additional_paths=additional_paths,
        no_skills=no_skills,
        resolved_resources=resolved_resources,
        extension_source_infos=extension_source_infos,
    )

    skills: Dict[str, Skill] = {}
    diagnostics: List[ResourceDiagnostic] = []

    for path, label, source_info in candidates:
        if os.path.isdir(path):
            loaded = load_skills_from_dir(
                path, source_label=label, source_info=source_info
            )
        else:
            skill = load_skill_from_file(
                path, source_label=label, source_info=source_info
            )
            loaded = [skill] if skill is not None else []

        for skill in loaded:
            if allowed_names is not None and skill.name not in allowed_names:
                continue

            existing = skills.get(skill.name)
            if existing is not None:
                diagnostics.append(
                    ResourceDiagnostic(
                        category="collision",
                        message=(
                            f"Skill '{skill.name}' from {skill.file_path} "
                            f"shadowed by {existing.file_path}"
                        ),
                        path=skill.file_path,
                        collision=ResourceCollision(
                            resource_type="skill",
                            name=skill.name,
                            winner_path=existing.file_path,
                            loser_path=skill.file_path,
                            winner_source=existing.source_label,
                            loser_source=skill.source_label,
                        ),
                    )
                )
            else:
                skills[skill.name] = skill

    return skills, diagnostics


__all__ = [
    "load_skills",
    "load_skill_from_file",
    "load_skills_from_dir",
    "validate_name",
    "validate_description",
]
