"""Skill 加载实现。

包含两个层次：

1. 文件级：发现 ``SKILL.md`` 文件并解析其 YAML frontmatter。
2. Resource 级：由 ``PackageResolver`` 提供路径，处理去重与冲突诊断。
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from nova_harness.core.resources.source_info import (
    default_source_info_for_path,
    find_source_info_for_path,
    source_info_from_metadata,
)
from nova_harness.core.types.extensions import SourceInfo
from nova_harness.core.types.package import (
    ResolvedResource,
    SourceOrigin,
    SourceScope,
)
from nova_harness.core.types.resources.diagnostics import (
    ResourceCollision,
    ResourceDiagnostic,
)
from nova_harness.core.types.resources.skills import Skill
from nova_harness.core.utils.files import canonicalize_path
from nova_harness.core.utils.frontmatter import parse_frontmatter
from nova_harness.package.utils import (
    SKIP_ENTRY_NAMES,
    IgnoreSpecWithPrefix,
    is_ignored_by_specs,
    load_ignore_specs,
)

_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")
_MAX_NAME_LEN = 64
_MAX_DESCRIPTION_LEN = 1024


def validate_name(name: str) -> List[str]:
    """校验 skill 名称，返回所有违反的规则（空列表表示合法）。

    规则与文案对齐 TS validateName（一次报全所有违规）。
    """
    errors: List[str] = []
    if not name:
        errors.append("name is required")
        return errors
    if len(name) > _MAX_NAME_LEN:
        errors.append(f"name exceeds {_MAX_NAME_LEN} characters ({len(name)})")
    if not _NAME_PATTERN.match(name):
        errors.append(
            "name contains invalid characters "
            "(must be lowercase a-z, 0-9, hyphens only)"
        )
    if name.startswith("-") or name.endswith("-"):
        errors.append("name must not start or end with a hyphen")
    if "--" in name:
        errors.append("name must not contain consecutive hyphens")
    return errors


def validate_description(description: str) -> List[str]:
    """校验 skill 描述，返回所有违反的规则（空列表表示合法）。"""
    errors: List[str] = []
    if not description or not description.strip():
        errors.append("description is required")
    elif len(description) > _MAX_DESCRIPTION_LEN:
        errors.append(
            f"description exceeds {_MAX_DESCRIPTION_LEN} characters "
            f"({len(description)})"
        )
    return errors


def load_skill_from_file(
    file_path: str,
    source_label: str = "unknown",
    source_info: Optional[SourceInfo] = None,
) -> Tuple[Optional[Skill], List[ResourceDiagnostic]]:
    """加载单个 skill 文件，返回 ``(skill, diagnostics)``。

    对齐 TS 的宽松模型：name 非法、description 超长只产生 warning，
    skill 照常加载；description 完全缺失或文件不可读时才拒载（附 warning）。
    """
    diagnostics: List[ResourceDiagnostic] = []

    def warn(message: str) -> None:
        diagnostics.append(
            ResourceDiagnostic(category="warning", message=message, path=file_path)
        )

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (IOError, UnicodeDecodeError) as exc:
        warn(f"failed to parse skill file: {exc}")
        return None, diagnostics

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

    # name 非法：warning 但照常加载（对齐 TS，一次报全所有违规）
    for message in validate_name(name):
        warn(message)

    # description 完全缺失：warning + 拒载；超长：warning + 加载（对齐 TS）
    if not isinstance(description, str) or not description.strip():
        warn("description is required")
        return None, diagnostics
    for message in validate_description(description):
        warn(message)

    # source_info 的 path 指向实际 skill 文件（对齐 TS findSourceInfoForPath
    # 的 path 更新语义），而不是共享的资源根路径。
    if source_info is not None:
        source_info = source_info.model_copy(update={"path": file_path})

    return (
        Skill(
            name=name,
            description=description,
            file_path=file_path,
            base_dir=str(Path(file_path).parent),
            disable_model_invocation=disable,
            source_label=source_label,
            source_info=source_info,
        ),
        diagnostics,
    )


def load_skills_from_dir(
    directory: str,
    source_label: str = "unknown",
    source_info: Optional[SourceInfo] = None,
) -> Tuple[List[Skill], List[ResourceDiagnostic]]:
    """递归扫描目录加载 skill，返回 ``(skills, diagnostics)``。

    发现规则（对齐 TS）：
    - 目录含 ``SKILL.md`` → 视为 skill 根，加载后不再递归其子目录；
    - 否则递归子目录，且第一层目录的散装 ``.md`` 文件也作为 skill 加载；
    - 跳过 ``.`` 开头条目与各生态依赖/缓存目录
      （``node_modules``/``__pycache__``/``venv``/``env``）；
    - 应用目录树中的 ignore 规则（.gitignore/.ignore/.fdignore）。
    """
    root_dir = str(Path(directory).resolve())
    specs = load_ignore_specs(root_dir)
    return _load_skills_from_dir_internal(
        directory,
        source_label=source_label,
        source_info=source_info,
        root_dir=root_dir,
        specs=specs,
        include_root_files=True,
    )


def _load_skills_from_dir_internal(
    directory: str,
    source_label: str,
    source_info: Optional[SourceInfo],
    root_dir: str,
    specs: List[IgnoreSpecWithPrefix],
    include_root_files: bool,
) -> Tuple[List[Skill], List[ResourceDiagnostic]]:
    skills: List[Skill] = []
    diagnostics: List[ResourceDiagnostic] = []
    root = Path(directory)
    if not root.exists() or not root.is_dir():
        return skills, diagnostics

    resolved_root = root.resolve()
    try:
        rel_prefix = str(resolved_root.relative_to(Path(root_dir).resolve()))
    except ValueError:
        rel_prefix = ""
    if rel_prefix:
        rel_prefix += "/"

    def load_one(entry: Path) -> None:
        skill, warns = load_skill_from_file(
            str(entry), source_label=source_label, source_info=source_info
        )
        diagnostics.extend(warns)
        if skill is not None:
            skills.append(skill)

    entries = sorted(root.iterdir())

    # 第一遍：SKILL.md —— 存在即视为 skill 根，加载后停止递归（对齐 TS）。
    # 被 ignore 的 SKILL.md 不算 skill 根，落入第二遍照常递归。
    for entry in entries:
        if entry.name != "SKILL.md" or not entry.is_file():
            continue
        entry_rel = f"{rel_prefix}{entry.name}"
        if is_ignored_by_specs(entry_rel, is_dir=False, specs=specs):
            continue
        load_one(entry)
        return skills, diagnostics

    # 第二遍：递归子目录（散装 .md 不再生效）+ 第一层散装 .md
    for entry in entries:
        # 跳过隐藏条目与各生态的依赖/缓存目录（共享名单，与包发现、
        # ignore 收集一致；. 开头另覆盖 .venv/.pixi 等）
        if entry.name.startswith(".") or entry.name in SKIP_ENTRY_NAMES:
            continue
        entry_rel = f"{rel_prefix}{entry.name}"

        if entry.is_dir():
            if is_ignored_by_specs(entry_rel, is_dir=True, specs=specs):
                continue
            sub_skills, sub_diagnostics = _load_skills_from_dir_internal(
                str(entry),
                source_label=source_label,
                source_info=source_info,
                root_dir=root_dir,
                specs=specs,
                include_root_files=False,
            )
            skills.extend(sub_skills)
            diagnostics.extend(sub_diagnostics)
            continue

        if (
            not entry.is_file()
            or not include_root_files
            or not entry.name.endswith(".md")
        ):
            continue
        if is_ignored_by_specs(entry_rel, is_dir=False, specs=specs):
            continue
        load_one(entry)

    return skills, diagnostics


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
    agent_dir: Optional[str] = None,
    cwd: Optional[str] = None,
) -> Tuple[List[Tuple[str, str, Optional[SourceInfo]]], List[ResourceDiagnostic]]:
    """Return ``(paths, diagnostics)`` to load skills from.

    路径优先级（对齐 pi 的单通道语义）：``resolved_resources``
    （settings/自动发现/包）> ``additional_paths``（CLI/SDK 显式传入与
    扩展贡献）。

    ``no_skills=True`` 只禁用 ``resolved_resources``，不禁用显式传入的路径。
    显式路径不存在时产生 warning 诊断。显式路径没有 resolver metadata 时，
    按标准资源根位置合成默认 ``SourceInfo``（对齐 TS
    ``getDefaultSourceInfoForPath``）。
    """
    paths: List[Tuple[str, str, Optional[SourceInfo]]] = []
    diagnostics: List[ResourceDiagnostic] = []
    seen: set = set()

    def add(
        path: str,
        label: str,
        source_info: Optional[SourceInfo] = None,
        warn_missing: bool = False,
    ) -> None:
        resolved = Path(path).resolve()
        if not resolved.exists():
            # 显式传入的路径不存在时给 warning（对齐 TS）；
            # 自动发现的资源路径不存在则静默
            if warn_missing:
                diagnostics.append(
                    ResourceDiagnostic(
                        category="warning",
                        message="skill path does not exist",
                        path=str(resolved),
                    )
                )
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
        if source_info is None:
            source_info = default_source_info_for_path(
                str(resolved), agent_dir=agent_dir, cwd=cwd
            )
        add(p, "path", source_info, warn_missing=True)

    return paths, diagnostics


def load_skills(
    additional_paths: Optional[List[str]] = None,
    no_skills: bool = False,
    resolved_resources: Optional[List[ResolvedResource]] = None,
    extension_source_infos: Optional[List[SourceInfo]] = None,
    agent_dir: Optional[str] = None,
    cwd: Optional[str] = None,
) -> Tuple[Dict[str, Skill], List[ResourceDiagnostic]]:
    """加载所有可用 skill。

    返回 ``(skills_by_name, diagnostics)``。同名 skill 按优先级保留第一个，
    后续重复项生成 collision 诊断。

    路径必须由 ``PackageResolver`` 提供；``additional_paths`` 作为补充追加。
    白名单裁剪不在加载层——由消费侧
    （``harness/skills.filter_skills_by_whitelist``）统一执行。

    Args:
        agent_dir / cwd: 用于为 additional 路径合成默认 ``SourceInfo`` 的
            全局/项目基准目录。
    """
    candidates, diagnostics = _collect_skill_paths(
        additional_paths=additional_paths,
        no_skills=no_skills,
        resolved_resources=resolved_resources,
        extension_source_infos=extension_source_infos,
        agent_dir=agent_dir,
        cwd=cwd,
    )

    skills: Dict[str, Skill] = {}

    for path, label, source_info in candidates:
        if os.path.isdir(path):
            loaded, warns = load_skills_from_dir(
                path, source_label=label, source_info=source_info
            )
            diagnostics.extend(warns)
        elif path.endswith(".md"):
            skill, warns = load_skill_from_file(
                path, source_label=label, source_info=source_info
            )
            diagnostics.extend(warns)
            loaded = [skill] if skill is not None else []
        else:
            # 显式路径既不是目录也不是 markdown 文件（对齐 TS warning）
            diagnostics.append(
                ResourceDiagnostic(
                    category="warning",
                    message="skill path is not a markdown file",
                    path=path,
                )
            )
            continue

        for skill in loaded:
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
