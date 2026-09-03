"""会话上下文重建（对齐 TS ``session/context.ts``）。

把分支路径上的 entry 流水重新物化为喂给 LLM 的上下文：

- **状态派生**：沿路径扫描，最新 ``thinking_level_change`` / ``model_change``
  / ``active_tools_change``（或 assistant 消息自带的 provider/model）决定当前
  设置；
- **压缩边界**：默认只从**最近一次 compaction** 起步——摘要消息 + 其
  ``retained_tail``，更早历史被摘要替代；
- **条目投影**：message 原样入上下文（``stop_reason="deferred"`` 的占位
  assistant 消息跳过）；compaction → 摘要消息 + retained_tail；branch_summary
  → 摘要消息；custom → 经注册的 projector 投影（无注册则省略）。

summary 消息以 dict 形态产出（nova 方言 ``role="compaction_summary"`` /
``role="branch_summary"``，对齐 pi 的专用 role）——渲染方式由消费层决定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

__all__ = [
    "ContextEntryTransform",
    "SessionContext",
    "SessionContextBuildOptions",
    "build_context_entries",
    "build_session_context",
    "default_context_entry_transform",
    "derive_session_context_state",
    "session_entry_to_context_messages",
]

ContextEntryTransform = Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]
CustomEntryContextMessageProjector = Callable[..., Optional[List[Dict[str, Any]]]]


@dataclass
class SessionContextBuildOptions:
    entry_transforms: List[ContextEntryTransform] = field(default_factory=list)
    entry_projectors: Dict[str, CustomEntryContextMessageProjector] = field(default_factory=dict)


@dataclass
class SessionContext:
    """重建后的会话上下文（消息流水 + 当前设置）。"""

    messages: List[Dict[str, Any]]
    thinking_level: str = "off"
    model: Optional[Dict[str, Any]] = None
    active_tool_names: Optional[List[str]] = None


def derive_session_context_state(path_entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """沿路径派生当前 thinking level / model / active tools（latest-wins）。"""
    thinking_level = "off"
    model: Optional[Dict[str, Any]] = None
    active_tool_names: Optional[List[str]] = None

    for entry in path_entries:
        entry_type = entry.get("type")
        if entry_type == "thinking_level_change":
            thinking_level = entry["thinking_level"]
        elif entry_type == "model_change":
            model = {"provider": entry["provider"], "model_id": entry["model_id"]}
        elif entry_type == "message" and entry.get("message", {}).get("role") == "assistant":
            message = entry["message"]
            model = {"provider": message.get("provider"), "model_id": message.get("model")}
        elif entry_type == "active_tools_change":
            active_tool_names = list(entry["active_tool_names"])

    return {"thinking_level": thinking_level, "model": model, "active_tool_names": active_tool_names}


def default_context_entry_transform(path_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """默认变换：最近一次 compaction 起步（摘要 + 其后全部条目）。"""
    compaction: Optional[Dict[str, Any]] = None
    compaction_index = -1
    for index in range(len(path_entries) - 1, -1, -1):
        entry = path_entries[index]
        if entry.get("type") == "compaction":
            compaction = entry
            compaction_index = index
            break
    if compaction is None:
        return list(path_entries)
    return [compaction, *path_entries[compaction_index + 1 :]]


def build_context_entries(
    path_entries: List[Dict[str, Any]],
    options: Optional[SessionContextBuildOptions] = None,
) -> List[Dict[str, Any]]:
    opts = options or SessionContextBuildOptions()
    entries = default_context_entry_transform(path_entries)
    for transform in opts.entry_transforms:
        entries = transform(entries)
    return entries


def session_entry_to_context_messages(
    entry: Dict[str, Any],
    index: int,
    entries: List[Dict[str, Any]],
    options: Optional[SessionContextBuildOptions] = None,
) -> List[Dict[str, Any]]:
    opts = options or SessionContextBuildOptions()
    entry_type = entry.get("type")
    if entry_type == "message":
        message = entry["message"]
        if message.get("role") == "assistant" and message.get("stop_reason") == "deferred":
            return []
        return [message]
    if entry_type == "compaction":
        return [
            {
                "role": "compaction_summary",
                "summary": entry["summary"],
                "tokens_before": entry["tokens_before"],
                "timestamp": entry.get("timestamp"),
            },
            *entry.get("retained_tail", []),
        ]
    if entry_type == "branch_summary" and entry.get("summary"):
        return [
            {
                "role": "branch_summary",
                "summary": entry["summary"],
                "from_id": entry["from_id"],
                "timestamp": entry.get("timestamp"),
            }
        ]
    if entry_type == "custom":
        projector = opts.entry_projectors.get(entry.get("custom_type", ""))
        projected = projector(entry, index, entries) if projector is not None else None
        return list(projected or [])
    return []


def build_session_context(
    path_entries: List[Dict[str, Any]],
    options: Optional[SessionContextBuildOptions] = None,
    *,
    entry_transforms: Optional[List[ContextEntryTransform]] = None,
    entry_projectors: Optional[Dict[str, CustomEntryContextMessageProjector]] = None,
) -> SessionContext:
    if options is None and (entry_transforms is not None or entry_projectors is not None):
        options = SessionContextBuildOptions(
            entry_transforms=entry_transforms or [],
            entry_projectors=entry_projectors or {},
        )
    state = derive_session_context_state(path_entries)
    context_entries = build_context_entries(path_entries, options)
    messages: List[Dict[str, Any]] = []
    for index, entry in enumerate(context_entries):
        messages.extend(session_entry_to_context_messages(entry, index, context_entries, options))
    return SessionContext(
        messages=messages,
        thinking_level=state["thinking_level"],
        model=state["model"],
        active_tool_names=state["active_tool_names"],
    )
