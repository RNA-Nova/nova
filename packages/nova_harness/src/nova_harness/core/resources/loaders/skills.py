"""Skill 加载实现。

包含两个层次：

1. 文件级：发现 ``SKILL.md`` 文件并解析其 YAML frontmatter。
2. Resource 级：按 Nova 资源优先级（additional -> settings -> project -> global）
   发现并加载 skill，处理去重与冲突诊断。
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from nova_harness.core.config.defaults import CONFIG_DIR_NAME
from nova_harness.core.types.diagnostics import ResourceCollision, ResourceDiagnostic
from nova_harness.core.types.skills import Skill
from nova_harness.core.utils.frontmatter import parse_frontmatter

_NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_MAX_NAME_LEN = 64
_MAX_DESCRIPTION_LEN = 1024


# =============================================================================
# 文件级加载
# =============================================================================


def validate_name(name: str) -> Tuple[bool, str]:
    """校验 skill 名称是否合法。"""
    if not name:
        return False, "Skill name is required"
    if len(name) > _MAX_NAME_LEN:
        return False, f"Skill name exceeds {_MAX_NAME_LEN} characters"
    if not _NAME_PATTERN.match(name):
        return (
            False,
            "Skill name must be lowercase alphanumeric with hyphens only",
        )
    if name.startswith("-") or name.endswith("-"):
        return False, "Skill name must not start or end with a hyphen"
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
    file_path: str, source_label: str = "unknown"
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

    name = fm.get("name") if isinstance(fm, dict) else None
    description = fm.get("description") if isinstance(fm, dict) else None
    disable = (
        bool(fm.get("disable-model-invocation")) if isinstance(fm, dict) else False
    )

    if not isinstance(name, str):
        return None
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
    )


def load_skills_from_dir(directory: str, source_label: str = "unknown") -> List[Skill]:
    """递归扫描目录，加载所有 ``SKILL.md``。

    如果某个目录包含 ``SKILL.md``，则停止继续递归该目录的子目录。
    """
    skills: List[Skill] = []
    root = Path(directory)
    if not root.exists() or not root.is_dir():
        return skills

    for entry in sorted(root.iterdir()):
        if entry.is_file() and entry.name == "SKILL.md":
            skill = load_skill_from_file(str(entry), source_label=source_label)
            if skill is not None:
                skills.append(skill)
        elif entry.is_dir():
            skill_file = entry / "SKILL.md"
            if skill_file.exists():
                skill = load_skill_from_file(str(skill_file), source_label=source_label)
                if skill is not None:
                    skills.append(skill)
            else:
                skills.extend(
                    load_skills_from_dir(str(entry), source_label=source_label)
                )

    return skills


# =============================================================================
# Resource 级加载
# =============================================================================


def _collect_skill_paths(
    cwd: str,
    agent_dir: str,
    settings_manager: Optional[Any],
    additional_paths: Optional[List[str]],
    no_skills: bool,
) -> List[Tuple[str, str]]:
    """Return list of (path, source_label) to load skills from."""
    if no_skills:
        return []

    paths: List[Tuple[str, str]] = []
    seen: set = set()

    def add(path: str, label: str) -> None:
        resolved = Path(path).resolve()
        if resolved.exists() and str(resolved) not in seen:
            seen.add(str(resolved))
            paths.append((str(resolved), label))

    # 1. 显式配置路径
    for p in additional_paths or []:
        add(p, "path")

    # 2. settings 中配置的 skill paths
    if settings_manager is not None:
        for p in settings_manager.get_skill_paths():
            add(p, "settings")

    # 3. 项目级自动发现
    project_skills = Path(cwd) / CONFIG_DIR_NAME / "skills"
    if project_skills.exists():
        add(str(project_skills), "project")

    # 4. 全局自动发现
    global_skills = Path(agent_dir) / "skills"
    if global_skills.exists():
        add(str(global_skills), "global")

    return paths


def load_skills(
    cwd: str,
    agent_dir: str,
    settings_manager: Optional[Any] = None,
    additional_paths: Optional[List[str]] = None,
    no_skills: bool = False,
) -> Tuple[Dict[str, Skill], List[ResourceDiagnostic]]:
    """加载所有可用 skill。

    返回 ``(skills_by_name, diagnostics)``。同名 skill 按优先级保留第一个，
    后续重复项生成 collision 诊断。
    """
    candidates = _collect_skill_paths(
        cwd=cwd,
        agent_dir=agent_dir,
        settings_manager=settings_manager,
        additional_paths=additional_paths,
        no_skills=no_skills,
    )

    skills: Dict[str, Skill] = {}
    diagnostics: List[ResourceDiagnostic] = []

    for path, label in candidates:
        if os.path.isdir(path):
            loaded = load_skills_from_dir(path, source_label=label)
        else:
            skill = load_skill_from_file(path, source_label=label)
            loaded = [skill] if skill is not None else []

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
