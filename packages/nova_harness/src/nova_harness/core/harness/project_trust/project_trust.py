"""Project Trust 决策逻辑。"""

from pathlib import Path
from typing import Any, Callable, Optional

from nova_harness.core.config.defaults import CONFIG_DIR_NAME, SETTINGS_FILE_NAME
from nova_harness.core.harness.project_trust.trust_store import ProjectTrustStore
from nova_harness.core.utils.git import find_git_root
from nova_harness.core.types.project_trust import (
    ProjectTrustContext,
    ProjectTrustEvent,
    ProjectTrustEventResult,
    ProjectTrustOption,
    ProjectTrustUpdate,
    ResolveProjectTrustedOptions,
)
from nova_harness.core.types.ui import UIContext

TRUST_REQUIRING_RESOURCES = {
    SETTINGS_FILE_NAME,
    "extensions",
    "skills",
    "prompts",
    "themes",
    "SYSTEM.md",
    "APPEND_SYSTEM.md",
}


def has_trust_requiring_project_resources(cwd: str) -> bool:
    """检查 cwd 下或项目根（git root）内是否存在需要项目信任门控的资源。

    - ``.nova`` 目录本身不触发信任；只有其中存在具体资源
      （settings/extensions/skills/prompts/themes/SYSTEM.md/APPEND_SYSTEM.md）时才触发。
    - cwd 或其祖先目录中存在 ``.agents/skills`` 目录时也触发；空目录也算。
      用户主目录下的 ``~/.agents/skills`` 明确排除。
    - 向上遍历时遇到 git 仓库根目录即停止，避免爬到文件系统根而误中无关目录。
      不在 git 仓库内时，只检查 cwd 自身。
    """
    project_config_dir = Path(cwd).resolve() / CONFIG_DIR_NAME
    if project_config_dir.is_dir():
        for entry in TRUST_REQUIRING_RESOURCES:
            if (project_config_dir / entry).exists():
                return True

    current = Path(cwd).resolve()
    home = Path.home().resolve()
    user_agents_skills = (home / ".agents" / "skills").resolve()

    # 只在当前项目范围内（git root 或 cwd 自身）检查 .agents/skills。
    stop_path = find_git_root(cwd) or current

    while True:
        agents_skills = (current / ".agents" / "skills").resolve()
        if agents_skills != user_agents_skills and agents_skills.is_dir():
            return True
        if current == stop_path:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    return False


def get_project_trust_parent_path(cwd: str) -> Optional[str]:
    """返回 cwd 的父目录路径；如果已到根目录则返回 None。"""
    current = Path(cwd).resolve()
    parent = current.parent
    if parent == current:
        return None
    return str(parent)


def get_project_trust_options(
    cwd: str, include_session_only: bool = False
) -> list[ProjectTrustOption]:
    """生成展示给用户的 project trust 选项。"""
    trust_path = str(Path(cwd).resolve())
    options: list[ProjectTrustOption] = [
        ProjectTrustOption(
            label="Trust",
            trusted=True,
            updates=[ProjectTrustUpdate(path=trust_path, decision=True)],
            saved_path=trust_path,
        ),
    ]

    parent_path = get_project_trust_parent_path(cwd)
    if parent_path is not None:
        options.append(
            ProjectTrustOption(
                label=f"Trust parent folder ({parent_path})",
                trusted=True,
                updates=[
                    ProjectTrustUpdate(path=parent_path, decision=True),
                    ProjectTrustUpdate(path=trust_path, decision=None),
                ],
                saved_path=parent_path,
            )
        )

    if include_session_only:
        options.append(
            ProjectTrustOption(
                label="Trust (this session only)",
                trusted=True,
                updates=[],
            )
        )

    options.append(
        ProjectTrustOption(
            label="Do not trust",
            trusted=False,
            updates=[ProjectTrustUpdate(path=trust_path, decision=False)],
            saved_path=trust_path,
        )
    )

    if include_session_only:
        options.append(
            ProjectTrustOption(
                label="Do not trust (this session only)",
                trusted=False,
                updates=[],
            )
        )

    return options


def _format_prompt(cwd: str) -> str:
    return (
        f"Trust project folder?\n{cwd}\n\n"
        "This allows Nova to load .nova settings and resources, "
        "install missing project packages, and execute project extensions."
    )


async def _select_project_trust_option(
    cwd: str, ui: UIContext
) -> Optional[ProjectTrustOption]:
    options = get_project_trust_options(cwd, include_session_only=True)
    labels = [option.label for option in options]
    selected = await ui.select(_format_prompt(cwd), labels)
    if selected is None:
        return None
    for option in options:
        if option.label == selected:
            return option
    return None


def _save_option_result(
    trust_store: ProjectTrustStore, option: ProjectTrustOption
) -> None:
    if option.updates:
        trust_store.set_many(option.updates)


async def emit_project_trust_event(
    extensions_result: Any,
    event: ProjectTrustEvent,
    context: ProjectTrustContext,
    on_error: Optional[Callable[[str], None]] = None,
) -> tuple[Optional[ProjectTrustEventResult], list[str]]:
    """触发扩展的 project_trust 事件并收集结果与错误。

    直接消费扩展加载结果中的 extensions 列表，不再依赖 ExtensionRunner。
    第一个返回 ``yes``/``no`` 的 handler 获胜；返回 ``undecided`` 的 handler 会
    被跳过，继续询问后续扩展。
    """
    errors: list[str] = []

    extensions = getattr(extensions_result, "extensions", []) or []
    for extension in extensions:
        handlers = extension.handlers.get("project_trust", [])
        for handler in handlers:
            try:
                raw = handler(event, context)
                if hasattr(raw, "__await__"):
                    raw = await raw
                if raw is None:
                    continue

                if isinstance(raw, dict):
                    trusted_value = raw.get("trusted", False)
                    remember_value = bool(raw.get("remember", False))
                else:
                    trusted_value = getattr(raw, "trusted", False)
                    remember_value = bool(getattr(raw, "remember", False))

                if trusted_value == "undecided":
                    continue

                trusted = "yes" if trusted_value in (True, "yes") else "no"
                return ProjectTrustEventResult(
                    trusted=trusted,
                    remember=remember_value,
                ), errors
            except Exception as exc:
                msg = f'Extension "{extension.path}" project_trust error: {exc}'
                errors.append(msg)
                if on_error is not None:
                    try:
                        on_error(msg)
                    except Exception:
                        pass

    return None, errors


async def resolve_project_trusted(options: ResolveProjectTrustedOptions) -> bool:
    """根据优先级链决定项目是否被信任。"""
    cwd = options.cwd

    if options.trust_override is not None:
        return options.trust_override

    if not has_trust_requiring_project_resources(cwd):
        return True

    context = options.project_trust_context
    if context is not None and options.extensions_result is not None:
        event_result, _ = await emit_project_trust_event(
            options.extensions_result,
            ProjectTrustEvent(cwd=cwd),
            context,
            options.on_extension_error,
        )
        if event_result is not None:
            trusted = event_result.trusted == "yes"
            if event_result.remember:
                options.trust_store.set(cwd, trusted)
            return trusted

    decision = options.trust_store.get(cwd)
    if decision is not None:
        return decision

    default = options.default_project_trust
    if default == "always":
        return True
    if default == "never":
        return False

    if context is None or not context.has_ui:
        # 无 UI 时默认不信任。
        return False

    selected = await _select_project_trust_option(cwd, context.ui)
    if selected is not None:
        _save_option_result(options.trust_store, selected)
        return selected.trusted

    return False
