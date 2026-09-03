"""会话上下文重建测试（对齐 pi ``context.test.ts``）。"""

from typing import Any, Dict

from nova_agent.harness.session import build_session_context


def _user_message(text: str) -> Dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": text}], "timestamp": 1}


def _assistant_message(text: str) -> Dict[str, Any]:
    return {
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "api": "anthropic-messages",
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
        "usage": {
            "input": 0,
            "output": 0,
            "cache_read": 0,
            "cache_write": 0,
            "total_tokens": 0,
            "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0},
        },
        "stop_reason": "stop",
        "timestamp": 1,
    }


def _entry(value: Dict[str, Any], seq: int) -> Dict[str, Any]:
    return {**value, "seq": seq, "timestamp": seq}


def test_starts_at_latest_compaction_and_materializes_retained_tail() -> None:
    entries = [
        _entry({"type": "message", "id": "old", "parent_id": None, "message": _user_message("old")}, 1),
        _entry(
            {
                "type": "compaction",
                "id": "compact",
                "parent_id": "old",
                "summary": "summary",
                "retained_tail": [_user_message("retained"), _assistant_message("answer")],
                "tokens_before": 100,
            },
            2,
        ),
        _entry(
            {"type": "model_change", "id": "model", "parent_id": "compact", "provider": "openai", "model_id": "gpt-5"},
            3,
        ),
        _entry(
            {"type": "thinking_level_change", "id": "thinking", "parent_id": "model", "thinking_level": "high"}, 4
        ),
        _entry({"type": "message", "id": "tail", "parent_id": "thinking", "message": _user_message("tail")}, 5),
    ]

    context = build_session_context(entries)
    assert [message["role"] for message in context.messages] == [
        "compaction_summary",
        "user",
        "assistant",
        "user",
    ]
    assert context.model == {"provider": "openai", "model_id": "gpt-5"}
    assert context.thinking_level == "high"


def test_applies_caller_transforms_after_compaction_boundary() -> None:
    entries = [
        _entry({"type": "message", "id": "old", "parent_id": None, "message": _user_message("old")}, 1),
        _entry(
            {
                "type": "compaction",
                "id": "compact",
                "parent_id": "old",
                "summary": "summary",
                "retained_tail": [],
                "tokens_before": 100,
            },
            2,
        ),
        _entry(
            {
                "type": "branch_summary",
                "id": "branch",
                "parent_id": "compact",
                "from_id": "abandoned",
                "summary": "branch summary",
            },
            3,
        ),
        _entry({"type": "message", "id": "tail", "parent_id": "branch", "message": _user_message("tail")}, 4),
    ]

    context = build_session_context(
        entries,
        entry_transforms=[lambda context_entries: [e for e in context_entries if e["type"] != "compaction"]],
    )
    assert [message["role"] for message in context.messages] == ["branch_summary", "user"]


def test_projects_custom_entries_and_omits_deferred_assistant_handles() -> None:
    deferred = {
        **_assistant_message(""),
        "content": [],
        "stop_reason": "deferred",
        "deferred": {"provider": "openai", "model_id": "gpt-5", "api": "openai-responses", "id": "response-1"},
    }
    entries = [
        _entry({"type": "message", "id": "user", "parent_id": None, "message": _user_message("hello")}, 1),
        _entry({"type": "message", "id": "deferred", "parent_id": "user", "message": deferred}, 2),
        _entry({"type": "custom", "id": "custom", "parent_id": "deferred", "custom_type": "note", "data": "project me"}, 3),
    ]

    context = build_session_context(
        entries,
        entry_projectors={
            "note": lambda custom, index, entries: [_user_message(f"note: {custom['data']}")],
        },
    )
    assert [message["role"] for message in context.messages] == ["user", "user"]
    assert context.messages[1]["content"] == [{"type": "text", "text": "note: project me"}]
