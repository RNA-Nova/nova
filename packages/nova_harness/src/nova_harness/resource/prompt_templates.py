from dataclasses import dataclass
import os
import re
from pathlib import Path
from typing import List, Optional, Dict, Any, Union

from ..config import CONFIG_DIR_NAME, get_prompts_dir
from .utils import parse_frontmatter
from .types import PromptTemplate, LoadPromptTemplatesOptions

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
    - ${@:N} for args from Nth onwards (bash-style slicing)
    - ${@:N:L} for L args starting from Nth
    
    Note: Replacement happens on the template string only. Argument values
    containing patterns like $1, $@, or $ARGUMENTS are NOT recursively substituted.
    """
    result = content

    # Replace $1, $2, etc. with positional args FIRST (before wildcards)
    # This prevents wildcard replacement values containing $<digit> patterns from being re-substituted
    
    def replace_positional(match):
        num = int(match.group(1))
        index = num - 1
        return args[index] if index < len(args) else ""
    
    result = re.sub(r'\$(\d+)', replace_positional, result)

    # Replace ${@:start} or ${@:start:length} with sliced args (bash-style)
    # Process BEFORE simple $@ to avoid conflicts
    
    def replace_sliced(match):
        start_str = match.group(1)
        length_str = match.group(2)
        
        start = int(start_str) - 1  # Convert to 0-indexed (user provides 1-indexed)
        # Treat 0 as 1 (bash convention: args start at 1)
        if start < 0:
            start = 0

        if length_str:
            length = int(length_str)
            return " ".join(args[start:start + length])
        return " ".join(args[start:])
    
    result = re.sub(r'\$\{@:(\d+)(?::(\d+))?\}', replace_sliced, result)

    # Pre-compute all args joined (optimization)
    all_args = " ".join(args)

    # Replace $ARGUMENTS with all args joined (new syntax, aligns with Claude, Codex, OpenCode)
    result = result.replace("$ARGUMENTS", all_args)

    # Replace $@ with all args joined (existing syntax)
    result = result.replace("$@", all_args)

    return result


def _load_template_from_file(file_path: str, source: str, source_label: str) -> Optional[PromptTemplate]:
    """Load a single template from a markdown file."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw_content = f.read()
        
        result = parse_frontmatter(raw_content)
        frontmatter = result.frontmatter
        body = result.body
        name = Path(file_path).stem  # Remove .md extension

        # Get description from frontmatter or first non-empty line
        description = frontmatter.get("description", "")
        if not description:
            first_line = next((line for line in body.split("\n") if line.strip()), "")
            if first_line:
                # Truncate if too long
                description = first_line[:60]
                if len(first_line) > 60:
                    description += "..."

        # Append source to description
        description = f"{description} {source_label}" if description else source_label

        return PromptTemplate(
            name=name,
            description=description,
            content=body,
            source=source,
            file_path=file_path
        )
    except Exception:
        return None


def _load_templates_from_dir(dir_path: str, source: str, source_label: str) -> List[PromptTemplate]:
    """Scan a directory for .md files (non-recursive) and load them as prompt templates."""
    templates: List[PromptTemplate] = []
    
    path = Path(dir_path)
    if not path.exists():
        return templates

    try:
        for entry in path.iterdir():
            full_path = entry
            
            # For symlinks, check if they point to a file
            is_file = entry.is_file()
            if entry.is_symlink():
                try:
                    is_file = full_path.resolve().is_file()
                except OSError:
                    # Broken symlink, skip it
                    continue
                        
            if is_file and entry.suffix == ".md":
                template = _load_template_from_file(str(full_path), source, source_label)
                print(template)
                if template:
                    templates.append(template)
                    
    except OSError:
        pass

    return templates


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


def _is_under_path(target: str, root: str) -> bool:
    """Check if target is under root directory."""
    try:
        normalized_root = Path(root).resolve()
        target_path = Path(target).resolve()
        
        if target_path == normalized_root:
            return True
            
        try:
            target_path.relative_to(normalized_root)
            return True
        except ValueError:
            return False
    except (OSError, ValueError):
        return False


def load_prompt_templates(options: Optional[LoadPromptTemplatesOptions] = None) -> List[PromptTemplate]:
    """
    Load all prompt templates from:
    1. Global: agent_dir/prompts/
    2. Project: cwd/{CONFIG_DIR_NAME}/prompts/
    3. Explicit prompt paths
    """
    if options is None:
        options = LoadPromptTemplatesOptions()
        
    resolved_cwd = options.cwd or os.getcwd()
    resolved_agent_dir = options.agent_dir or get_prompts_dir()
    prompt_paths = options.prompt_paths or []
    include_defaults = options.include_defaults if options.include_defaults is not None else True

    templates: List[PromptTemplate] = []

    # Setup base directories
    user_prompts_dir = os.path.join(options.agent_dir, "prompts") if options.agent_dir else resolved_agent_dir
    project_prompts_dir = os.path.join(resolved_cwd, CONFIG_DIR_NAME, "prompts")

    if include_defaults:
        # 1. Load global templates from agent_dir/prompts/
        # Note: if agent_dir is provided, it should be the agent dir, not the prompts dir
        global_prompts_dir = os.path.join(options.agent_dir, "prompts") if options.agent_dir else resolved_agent_dir
        templates.extend(_load_templates_from_dir(global_prompts_dir, "user", "(user)"))

        # 2. Load project templates from cwd/{CONFIG_DIR_NAME}/prompts/
        templates.extend(_load_templates_from_dir(project_prompts_dir, "project", "(project)"))

    def _get_source_info(resolved_path: str):
        """Determine source and label for a path."""
        if not include_defaults:
            if _is_under_path(resolved_path, user_prompts_dir):
                return {"source": "user", "label": "(user)"}
            if _is_under_path(resolved_path, project_prompts_dir):
                return {"source": "project", "label": "(project)"}
        return {"source": "path", "label": _build_path_source_label(resolved_path)}

    # 3. Load explicit prompt paths
    for raw_path in prompt_paths:
        resolved_path = _resolve_prompt_path(raw_path, resolved_cwd)
        
        if not os.path.exists(resolved_path):
            continue

        try:
            path = Path(resolved_path)
            source_info = _get_source_info(resolved_path)
            
            if path.is_dir():
                templates.extend(_load_templates_from_dir(
                    resolved_path, 
                    source_info["source"], 
                    source_info["label"]
                ))
            elif path.is_file() and resolved_path.endswith(".md"):
                template = _load_template_from_file(
                    resolved_path, 
                    source_info["source"], 
                    source_info["label"]
                )
                if template:
                    templates.append(template)
        except OSError:
            # Ignore read failures
            continue

    return templates


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
        args_string = text[space_index + 1:]

    template = next((t for t in templates if t.name == template_name), None)
    if template:
        args = parse_command_args(args_string)
        return substitute_args(template.content, args)

    return text