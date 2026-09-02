"""
提示词模板加载的 resource 调用层。

负责从文件系统发现、解析提示词模板，并做资源级别的去重与冲突诊断。
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from nova_harness.core.package.resolve.discovery import collect_prompt_entries
from nova_harness.core.resources.source_info import (
    default_source_info_for_path,
    find_source_info_for_path,
    source_info_from_metadata,
)
from nova_harness.core.types.extensions import SourceInfo
from nova_harness.core.types.package import ResolvedResource
from nova_harness.core.types.resources.diagnostics import (
    ResourceCollision,
    ResourceDiagnostic,
)
from nova_harness.core.types.resources.extension_paths import ResourceExtensionPathEntry
from nova_harness.core.types.resources.prompts import (
    LoadPromptTemplatesOptions,
    PromptTemplate,
)
from nova_harness.core.utils.files import canonicalize_path
from nova_harness.core.utils.frontmatter import parse_frontmatter

# ---------------------------------------------------------------------------
# 命令参数解析 / 模板变量替换
# ---------------------------------------------------------------------------


def parse_command_args(args_string: str) -> List[str]:
    """
    Parse command arguments respecting quoted strings (bash-style).
    Returns array of arguments.
    """
    args: List[str] = []
    current = ""
    in_quote: Optional[str] = None

    for char in args_string:
        if in_quote:
            if char == in_quote:
                in_quote = None
            else:
                current += char
        elif char == '"' or char == "'":
            in_quote = char
        elif char == " " or char == "\t":
            if current:
                args.append(current)
                current = ""
        else:
            current += char

    if current:
        args.append(current)

    return args


def substitute_args(content: str, args: List[str]) -> str:
    """
    Substitute argument placeholders in template content.

    Supports:
    - $1, $2, ... for positional args
    - $@ and $ARGUMENTS for all args
    - ${N:-default} for positional arg N with default when missing or empty
    - ${@:N} for args from Nth onwards (bash-style slicing)
    - ${@:N:L} for L args starting from Nth

    Note: Replacement happens on the template string only. Argument values
    containing patterns like $1, $@, or $ARGUMENTS are NOT recursively substituted.
    """
    result = content

    def replace_default(match):
        num = int(match.group(1))
        default = match.group(2)
        index = num - 1
        value = args[index] if index < len(args) else ""
        return value if value else default

    result = re.sub(r"\$\{(\d+):-([^}]*)\}", replace_default, result)

    def replace_positional(match):
        num = int(match.group(1))
        index = num - 1
        return args[index] if index < len(args) else ""

    result = re.sub(r"\$(\d+)", replace_positional, result)

    def replace_sliced(match):
        start_str = match.group(1)
        length_str = match.group(2)

        start = int(start_str) - 1
        if start < 0:
            start = 0

        if length_str:
            length = int(length_str)
            return " ".join(args[start : start + length])
        return " ".join(args[start:])

    result = re.sub(r"\$\{@:(\d+)(?::(\d+))?\}", replace_sliced, result)

    all_args = " ".join(args)
    result = result.replace("$ARGUMENTS", all_args)
    result = result.replace("$@", all_args)

    return result


# ---------------------------------------------------------------------------
# 单文件加载
# ---------------------------------------------------------------------------


def _load_template_from_file(
    file_path: str,
    source: str,
    source_label: str,
    source_info: Optional[SourceInfo] = None,
) -> Tuple[Optional[PromptTemplate], List[ResourceDiagnostic]]:
    """Load a single template from a markdown file.

    读取/解析失败时返回 ``(None, [warning])``——与 skills 的宽松模型一致，
    畸形文件不再静默消失。
    """
    diagnostics: List[ResourceDiagnostic] = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw_content = f.read()

        result = parse_frontmatter(raw_content)
        frontmatter = result.frontmatter
        body = result.body
        name = Path(file_path).stem

        description = frontmatter.get("description", "")
        if not description:
            first_line = next((line for line in body.split("\n") if line.strip()), "")
            if first_line:
                description = first_line[:60]
                if len(first_line) > 60:
                    description += "..."

        description = f"{description} {source_label}" if description else source_label
        argument_hint = frontmatter.get("argument-hint")
        if not isinstance(argument_hint, str):
            argument_hint = None

        return (
            PromptTemplate(
                name=name,
                description=description,
                argument_hint=argument_hint,
                content=body,
                source=source,
                file_path=file_path,
                source_info=source_info,
            ),
            diagnostics,
        )
    except Exception as exc:
        diagnostics.append(
            ResourceDiagnostic(
                category="warning",
                message=f"failed to load prompt template: {exc}",
                path=file_path,
            )
        )
        return None, diagnostics


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------


def _normalize_path(input_path: str) -> str:
    """Normalize path, expanding ~ to home directory."""
    trimmed = input_path.strip()
    home = str(Path.home())

    if trimmed == "~":
        return home
    if trimmed.startswith("~/"):
        return os.path.join(home, trimmed[2:])
    if trimmed.startswith("~"):
        return os.path.join(home, trimmed[1:])
    return trimmed


def _resolve_prompt_path(p: str, cwd: str) -> str:
    """Resolve a prompt path relative to cwd if not absolute."""
    normalized = _normalize_path(p)
    path = Path(normalized)
    if path.is_absolute():
        return str(path)
    return str(Path(cwd).resolve() / path)


def _build_path_source_label(p: str) -> str:
    """Build a source label for explicitly provided paths."""
    base = Path(p).stem or "path"
    return f"(path:{base})"


# ---------------------------------------------------------------------------
# 对外 API：加载 + 去重 + 诊断
# ---------------------------------------------------------------------------


def load_prompt_templates(
    options: Optional[LoadPromptTemplatesOptions] = None,
    diagnostics: Optional[List[ResourceDiagnostic]] = None,
) -> List[PromptTemplate]:
    """
    从显式提供的路径加载 prompt templates。

    ``prompt_paths`` 中的目录会交给发现层（``collect_prompt_entries``）递归展开为
    ``.md`` 文件列表；本函数只负责单文件解析。路径必须由调用方（如
    ``PackageResolver``）或 CLI/扩展显式提供。

    Args:
        diagnostics: 提供时，单文件加载失败的 warning 诊断会追加到该列表。
    """
    if options is None:
        options = LoadPromptTemplatesOptions()

    resolved_cwd = options.cwd or os.getcwd()
    prompt_paths = options.prompt_paths or []
    resolved_resources = options.resolved_resources or []
    extension_source_infos = options.extension_source_infos or []

    templates: List[PromptTemplate] = []
    seen_paths: set = set()

    # 优先使用 resolver 提供的精确 metadata
    source_info_by_path: Dict[str, SourceInfo] = {}
    for resource in resolved_resources:
        if not resource.enabled:
            continue
        source_info_by_path[str(Path(resource.path).resolve())] = (
            source_info_from_metadata(resource)
        )

    def _get_source(resolved_path: str):
        """Determine source, label and SourceInfo for a path."""
        resolved = str(Path(resolved_path).resolve())
        if resolved in source_info_by_path:
            info = source_info_by_path[resolved]
            return {
                "source": info.source,
                "label": f"({info.scope})",
                "source_info": info,
            }

        # 扩展贡献路径按前缀匹配
        info = find_source_info_for_path(resolved, extension_source_infos)
        if info is not None:
            return {
                "source": info.source,
                "label": f"({info.scope})",
                "source_info": info,
            }

        # 无 metadata 的显式路径：按标准资源根位置合成默认 SourceInfo
        # （对齐 TS getDefaultSourceInfoForPath）。
        info = default_source_info_for_path(
            resolved, agent_dir=options.agent_dir, cwd=resolved_cwd
        )
        if info.scope == "user":
            return {"source": "user", "label": "(user)", "source_info": info}
        if info.scope == "project":
            return {"source": "project", "label": "(project)", "source_info": info}
        return {
            "source": "path",
            "label": _build_path_source_label(resolved_path),
            "source_info": info,
        }

    for raw_path in prompt_paths:
        resolved_path = _resolve_prompt_path(raw_path, resolved_cwd)

        if not os.path.exists(resolved_path):
            continue

        # 按真实路径去重：避免 symlink、相对/绝对路径重复加载同一文件
        real_path = canonicalize_path(resolved_path)
        if real_path in seen_paths:
            continue
        seen_paths.add(real_path)

        try:
            path = Path(resolved_path)

            if path.is_dir():
                # 目录递归由发现层负责；loader 只加载单文件。
                for file_path in collect_prompt_entries(resolved_path):
                    real_file_path = canonicalize_path(file_path)
                    if real_file_path in seen_paths:
                        continue
                    seen_paths.add(real_file_path)

                    file_source = _get_source(file_path)
                    template, warns = _load_template_from_file(
                        file_path,
                        file_source["source"],
                        file_source["label"],
                        source_info=file_source["source_info"],
                    )
                    if diagnostics is not None:
                        diagnostics.extend(warns)
                    if template:
                        templates.append(template)
            elif path.is_file() and resolved_path.endswith(".md"):
                source = _get_source(resolved_path)
                template, warns = _load_template_from_file(
                    resolved_path,
                    source["source"],
                    source["label"],
                    source_info=source["source_info"],
                )
                if diagnostics is not None:
                    diagnostics.extend(warns)
                if template:
                    templates.append(template)
        except OSError:
            continue

    return templates


def _dedupe_prompts(
    prompts: List[PromptTemplate],
) -> Dict[str, List[PromptTemplate] | List[ResourceDiagnostic]]:
    """去重提示词模板并记录冲突。"""
    seen: Dict[str, PromptTemplate] = {}
    diagnostics: List[ResourceDiagnostic] = []

    for prompt in prompts:
        existing = seen.get(prompt.name)
        if existing:
            diagnostics.append(
                ResourceDiagnostic(
                    category="collision",
                    message=f'name "/{prompt.name}" collision',
                    path=prompt.file_path,
                    collision=ResourceCollision(
                        resource_type="prompt",
                        name=prompt.name,
                        winner_path=existing.file_path,
                        loser_path=prompt.file_path,
                    ),
                )
            )
        else:
            seen[prompt.name] = prompt

    return {
        "prompts": list(seen.values()),
        "diagnostics": diagnostics,
    }


def load_prompt_templates_with_diagnostics(
    options: Optional[LoadPromptTemplatesOptions] = None,
) -> Dict[str, List[PromptTemplate] | List[ResourceDiagnostic]]:
    """加载并去重提示词模板，返回模板列表与诊断信息。"""
    load_diagnostics: List[ResourceDiagnostic] = []
    all_prompts = load_prompt_templates(options, diagnostics=load_diagnostics)
    deduped = _dedupe_prompts(all_prompts)
    return {
        "prompts": deduped["prompts"],
        "diagnostics": load_diagnostics + deduped["diagnostics"],
    }


def expand_prompt_template(text: str, templates: List[PromptTemplate]) -> str:
    """
    Expand a prompt template if it matches a template name.
    Returns the expanded content or the original text if not a template.
    """
    if not text.startswith("/"):
        return text

    space_index = text.find(" ")
    if space_index == -1:
        template_name = text[1:]
        args_string = ""
    else:
        template_name = text[1:space_index]
        args_string = text[space_index + 1 :]

    template = next((t for t in templates if t.name == template_name), None)
    if template:
        args = parse_command_args(args_string)
        return substitute_args(template.content, args)

    return text


__all__ = [
    "load_prompt_templates",
    "load_prompt_templates_with_diagnostics",
    "expand_prompt_template",
    "parse_command_args",
    "substitute_args",
]
