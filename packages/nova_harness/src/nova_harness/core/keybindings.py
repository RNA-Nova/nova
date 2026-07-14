"""应用级快捷键定义与冲突检测。

- 声明内置保留快捷键（reserved builtin keybindings），扩展不得覆盖
- 支持从 ``keybindings.json`` 加载用户自定义绑定
- 提供快捷键标准化与冲突检测工具
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from nova_harness.core.config.defaults import get_agent_dir
from nova_harness.core.types.keybindings import Keybinding, KeybindingDiagnostic


def _platform_default(keys: Union[str, List[str]]) -> Union[str, List[str]]:
    """返回当前平台适用的默认快捷键。"""
    if isinstance(keys, dict):
        default = keys.get("default", [])
        return keys.get(sys.platform, default)
    return keys


# 内置保留快捷键：扩展不能注册同名快捷键。
# 命名空间与 TS 的 KEYBINDINGS 对齐（app.* + tui.*）。
DEFAULT_KEYBINDINGS: List[Keybinding] = [
    # 应用生命周期
    Keybinding("app.interrupt", "escape", "Cancel or abort", reserved=True),
    Keybinding("app.clear", "ctrl+c", "Clear editor", reserved=True),
    Keybinding("app.exit", "ctrl+d", "Exit when editor is empty", reserved=True),
    Keybinding(
        "app.suspend",
        [] if sys.platform == "win32" else "ctrl+z",
        "Suspend to background",
        reserved=True,
    ),
    Keybinding("app.quit", "ctrl+q", "退出应用", reserved=True),
    Keybinding("app.submit", "return", "提交消息", reserved=True),
    Keybinding("app.cancel", "ctrl+c", "取消当前操作", reserved=True),
    Keybinding("app.new_session", "ctrl+n", "新建会话", reserved=True),
    Keybinding("app.toggle_menu", "ctrl+m", "切换菜单", reserved=True),
    # 模型与思考
    Keybinding("app.thinking.cycle", "shift+tab", "Cycle thinking level", reserved=True),
    Keybinding("app.model.cycleForward", "ctrl+p", "Cycle to next model", reserved=True),
    Keybinding("app.model.cycleBackward", "shift+ctrl+p", "Cycle to previous model", reserved=True),
    Keybinding("app.model.select", "ctrl+l", "Open model selector", reserved=True),
    Keybinding("app.tools.expand", "ctrl+o", "Toggle tool output", reserved=True),
    Keybinding("app.thinking.toggle", "ctrl+t", "Toggle thinking blocks", reserved=True),
    # 会话与树
    Keybinding("app.session.toggleNamedFilter", "ctrl+n", "Toggle named session filter", reserved=True),
    Keybinding("app.editor.external", "ctrl+g", "Open external editor", reserved=True),
    Keybinding("app.message.followUp", "alt+enter", "Queue follow-up message", reserved=True),
    Keybinding("app.message.dequeue", "alt+up", "Restore queued messages", reserved=True),
    Keybinding(
        "app.clipboard.pasteImage",
        "alt+v" if sys.platform == "win32" else "ctrl+v",
        "Paste image from clipboard",
        reserved=True,
    ),
    Keybinding("app.session.new", [], "Start a new session", reserved=True),
    Keybinding("app.session.tree", [], "Open session tree", reserved=True),
    Keybinding("app.session.fork", [], "Fork current session", reserved=True),
    Keybinding("app.session.resume", [], "Resume a session", reserved=True),
    Keybinding("app.tree.foldOrUp", ["ctrl+left", "alt+left"], "Fold tree branch or move up", reserved=True),
    Keybinding("app.tree.unfoldOrDown", ["ctrl+right", "alt+right"], "Unfold tree branch or move down", reserved=True),
    Keybinding("app.tree.editLabel", "shift+l", "Edit tree label", reserved=True),
    Keybinding("app.tree.toggleLabelTimestamp", "shift+t", "Toggle tree label timestamps", reserved=True),
    Keybinding("app.session.togglePath", "ctrl+p", "Toggle session path display", reserved=True),
    Keybinding("app.session.toggleSort", "ctrl+s", "Toggle session sort mode", reserved=True),
    Keybinding("app.session.rename", "ctrl+r", "Rename session", reserved=True),
    Keybinding("app.session.delete", "ctrl+d", "Delete session", reserved=True),
    Keybinding("app.session.deleteNoninvasive", "ctrl+backspace", "Delete session when query is empty", reserved=True),
    # 模型管理面板
    Keybinding("app.models.save", "ctrl+s", "Save model selection", reserved=True),
    Keybinding("app.models.enableAll", "ctrl+a", "Enable all models", reserved=True),
    Keybinding("app.models.clearAll", "ctrl+x", "Clear all models", reserved=True),
    Keybinding("app.models.toggleProvider", "ctrl+p", "Toggle all models for provider", reserved=True),
    Keybinding("app.models.reorderUp", "alt+up", "Move model up in order", reserved=True),
    Keybinding("app.models.reorderDown", "alt+down", "Move model down in order", reserved=True),
    # 树过滤器
    Keybinding("app.tree.filter.default", "ctrl+d", "Tree filter: default view", reserved=True),
    Keybinding("app.tree.filter.noTools", "ctrl+t", "Tree filter: hide tool results", reserved=True),
    Keybinding("app.tree.filter.userOnly", "ctrl+u", "Tree filter: user messages only", reserved=True),
    Keybinding("app.tree.filter.labeledOnly", "ctrl+l", "Tree filter: labeled entries only", reserved=True),
    Keybinding("app.tree.filter.all", "ctrl+a", "Tree filter: show all entries", reserved=True),
    Keybinding("app.tree.filter.cycleForward", "ctrl+o", "Tree filter: cycle forward", reserved=True),
    Keybinding("app.tree.filter.cycleBackward", "shift+ctrl+o", "Tree filter: cycle backward", reserved=True),
]


def _iter_defaults(keybinding: Keybinding) -> List[str]:
    """把 Keybinding.default 统一为字符串列表。"""
    raw = keybinding.default
    if isinstance(raw, list):
        return [str(k) for k in raw]
    if not raw:
        return []
    return [str(raw)]


def normalize_shortcut(shortcut: str) -> str:
    """标准化快捷键字符串，便于比较。"""
    parts = shortcut.lower().replace("+", " ").split()
    modifiers = {"ctrl", "shift", "alt", "meta", "cmd", "command"}
    mod_parts = sorted(p for p in parts if p in modifiers or p in {"cmd", "command"})
    key_parts = [p for p in parts if p not in modifiers and p not in {"cmd", "command"}]
    normalized_mods = []
    for m in mod_parts:
        if m in {"cmd", "command"}:
            normalized_mods.append("meta")
        else:
            normalized_mods.append(m)
    normalized_mods = sorted(set(normalized_mods))
    return "+".join(normalized_mods + key_parts)


def get_reserved_shortcuts() -> Set[str]:
    """返回所有 reserved builtin 快捷键的标准化集合。"""
    result: Set[str] = set()
    for kb in DEFAULT_KEYBINDINGS:
        if not kb.reserved:
            continue
        for default in _iter_defaults(kb):
            if default:
                result.add(normalize_shortcut(default))
    return result


def _order_keybindings_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """按默认快捷键顺序排列配置，额外键按字母顺序放在末尾。"""
    ordered: Dict[str, Any] = {}
    for kb in DEFAULT_KEYBINDINGS:
        if kb.id in config:
            ordered[kb.id] = config[kb.id]
    for key in sorted(config.keys()):
        if key not in ordered:
            ordered[key] = config[key]
    return ordered


def _coerce_binding(value: Any) -> Optional[Union[str, List[str]]]:
    """把用户配置中的单个绑定值归一化为字符串或字符串列表。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value
    return None


def load_user_keybindings(agent_dir: Optional[str] = None) -> Dict[str, Union[str, List[str]]]:
    """加载用户自定义 keybindings.json，返回 id -> shortcut 映射。"""
    base = Path(agent_dir) if agent_dir else Path(get_agent_dir())
    path = base / "keybindings.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {
            str(k): coerced
            for k, binding in data.items()
            if (coerced := _coerce_binding(binding)) is not None
        }
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_keybindings(
    agent_dir: Optional[str] = None,
    overrides: Optional[Dict[str, Union[str, List[str]]]] = None,
) -> Dict[str, Union[str, List[str]]]:
    """合并默认 keybindings 与用户覆盖，返回 id -> shortcut。"""
    resolved: Dict[str, Union[str, List[str]]] = {
        kb.id: _platform_default(kb.default) for kb in DEFAULT_KEYBINDINGS
    }
    resolved.update(load_user_keybindings(agent_dir))
    if overrides:
        resolved.update(overrides)
    return resolved


def is_reserved_shortcut(shortcut: str) -> bool:
    """判断快捷键是否与 reserved builtin 冲突。"""
    return normalize_shortcut(shortcut) in get_reserved_shortcuts()


def check_extension_shortcuts(
    shortcuts: List[Any],
    resolved_keybindings: Optional[Dict[str, Union[str, List[str]]]] = None,
) -> List[KeybindingDiagnostic]:
    """检测扩展快捷键与 reserved builtin / 用户绑定的冲突。"""
    diagnostics: List[KeybindingDiagnostic] = []
    seen: Dict[str, Any] = {}

    reserved = get_reserved_shortcuts()
    user_bindings: Set[str] = set()
    if resolved_keybindings:
        for binding in resolved_keybindings.values():
            if isinstance(binding, list):
                user_bindings.update(normalize_shortcut(b) for b in binding)
            elif isinstance(binding, str):
                user_bindings.add(normalize_shortcut(binding))

    for shortcut in shortcuts:
        raw = getattr(shortcut, "shortcut", shortcut)
        ext_path = getattr(shortcut, "extension_path", None)

        raw_values = raw if isinstance(raw, list) else [raw]
        for raw_value in raw_values:
            if not isinstance(raw_value, str):
                continue
            normalized = normalize_shortcut(raw_value)

            if normalized in reserved or normalized in user_bindings:
                diagnostics.append(
                    KeybindingDiagnostic(
                        type="warning",
                        message=f"Extension shortcut '{raw_value}' conflicts with builtin keybinding",
                        shortcut=raw_value,
                        extension_path=ext_path,
                    )
                )

            if normalized in seen:
                diagnostics.append(
                    KeybindingDiagnostic(
                        type="warning",
                        message=(
                            f"Extension shortcut '{raw_value}' registered by both "
                            f"{seen[normalized].extension_path or '<unknown>'} and {ext_path or '<unknown>'}"
                        ),
                        shortcut=raw_value,
                        extension_path=ext_path,
                    )
                )
            else:
                seen[normalized] = shortcut

    return diagnostics


__all__ = [
    "Keybinding",
    "KeybindingDiagnostic",
    "DEFAULT_KEYBINDINGS",
    "normalize_shortcut",
    "get_reserved_shortcuts",
    "load_user_keybindings",
    "resolve_keybindings",
    "is_reserved_shortcut",
    "check_extension_shortcuts",
]
