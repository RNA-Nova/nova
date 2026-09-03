"""包级用户工具消息的回载注册表与 JSONL 解析拦截测试。

覆盖：
- 注册表：注册/查询/清空、first-wins 碰撞、缺 role 跳过；
- 解析拦截：注册命中复原、未命中降级不透明、降级幂等、
  注册类校验失败降级、静态 role 不受影响；
- SessionMessageEntry 的 SerializeAsAny round-trip 与 dict 守卫。
"""

import json
from typing import Literal, Optional

import pytest
from nova_agent import CustomAgentMessage
from nova_harness.core.harness.session.message_types import (
    clear_session_message_types,
    get_session_message_type,
    register_user_tool_message_types,
)
from nova_harness.core.harness.session.utils import parse_session_entry_line
from nova_harness.core.types.messages import OpaqueUserToolMessage
from nova_harness.core.types.session.entries import SessionMessageEntry


class SearchResultMessage(CustomAgentMessage):
    """测试用包级用户工具消息。"""

    query: str
    count: int
    timestamp: int
    exclude_from_context: bool = False
    role: Literal["searchResult"] = "searchResult"

    def to_context_text(self) -> str:
        return f"Searched `{self.query}`: {self.count} results"


class NoRoleMessage(CustomAgentMessage):
    """缺少 role 字段的消息类（注册应跳过）。"""

    text: str = ""


def _line(role: str, **fields) -> str:
    return json.dumps(
        {
            "type": "message",
            "id": "e1",
            "parent_id": None,
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {"role": role, "timestamp": 123, **fields},
        }
    )


@pytest.fixture(autouse=True)
def clean_registry():
    clear_session_message_types()
    yield
    clear_session_message_types()


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------


def test_register_and_get():
    register_user_tool_message_types([SearchResultMessage])
    assert get_session_message_type("searchResult") is SearchResultMessage
    assert get_session_message_type("unknown") is None


def test_register_first_wins_on_role_collision():
    class OtherSearchMessage(CustomAgentMessage):
        role: Literal["searchResult"] = "searchResult"

    register_user_tool_message_types([SearchResultMessage, OtherSearchMessage])
    assert get_session_message_type("searchResult") is SearchResultMessage


def test_register_skips_class_without_role():
    register_user_tool_message_types([NoRoleMessage])
    assert get_session_message_type("noRole") is None


def test_clear_keeps_framework_opaque_type():
    clear_session_message_types()
    # 框架静态类型（opaque 降级形态）在清空后仍然可查（幂等解析的前提）
    assert get_session_message_type("opaqueUserTool") is OpaqueUserToolMessage


# ---------------------------------------------------------------------------
# 解析拦截
# ---------------------------------------------------------------------------


def test_parse_registered_message_restored():
    register_user_tool_message_types([SearchResultMessage])
    entry = parse_session_entry_line(_line("searchResult", query="nova", count=3))
    assert isinstance(entry, SessionMessageEntry)
    assert isinstance(entry.message, SearchResultMessage)
    assert entry.message.query == "nova"
    assert entry.message.count == 3


def test_parse_unregistered_message_degrades_to_opaque():
    entry = parse_session_entry_line(_line("searchResult", query="nova", count=3))
    msg = entry.message
    assert isinstance(msg, OpaqueUserToolMessage)
    assert msg.original_role == "searchResult"
    assert msg.payload["query"] == "nova"
    assert msg.exclude_from_context is True
    assert msg.timestamp == 123


def test_parse_invalid_registered_message_degrades_to_opaque():
    """注册类校验失败（包版本演进/数据腐坏）也降级，数据不丢。"""
    register_user_tool_message_types([SearchResultMessage])
    # count 缺失 → SearchResultMessage 校验失败
    entry = parse_session_entry_line(_line("searchResult", query="nova"))
    msg = entry.message
    assert isinstance(msg, OpaqueUserToolMessage)
    assert msg.original_role == "searchResult"
    assert msg.payload["query"] == "nova"


def test_parse_opaque_message_is_idempotent():
    """降级形态序列化后再解析不双重包装。"""
    entry = parse_session_entry_line(_line("searchResult", query="nova", count=3))
    dumped = entry.model_dump(mode="json")
    reparsed = parse_session_entry_line(json.dumps(dumped))
    msg = reparsed.message
    assert isinstance(msg, OpaqueUserToolMessage)
    assert msg.original_role == "searchResult"
    # payload 仍是原始消息 dict，不是一层 opaque 外壳
    assert msg.payload["query"] == "nova"
    assert "payload" not in msg.payload


def test_parse_static_roles_unaffected():
    line = json.dumps(
        {
            "type": "message",
            "id": "u1",
            "parent_id": None,
            "timestamp": "t",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "hi"}],
                "timestamp": 1,
            },
        }
    )
    entry = parse_session_entry_line(line)
    assert entry.message.role == "user"


def test_parse_malformed_standard_message_returns_none():
    """标准 role 的畸形消息不会被吞成空消息（dict 守卫生效）。"""
    line = json.dumps(
        {
            "type": "message",
            "id": "bad1",
            "parent_id": None,
            "timestamp": "t",
            "message": {"role": "user", "content": 12345, "timestamp": 1},
        }
    )
    assert parse_session_entry_line(line) is None


# ---------------------------------------------------------------------------
# SerializeAsAny round-trip
# ---------------------------------------------------------------------------


def test_entry_serializes_registered_message_with_own_schema():
    register_user_tool_message_types([SearchResultMessage])
    entry = parse_session_entry_line(_line("searchResult", query="nova", count=3))
    dumped = entry.model_dump(mode="json")
    assert dumped["message"]["role"] == "searchResult"
    assert dumped["message"]["query"] == "nova"
    # 完整 round-trip：dump 后再解析仍是注册类型
    reparsed = parse_session_entry_line(json.dumps(dumped))
    assert isinstance(reparsed.message, SearchResultMessage)


def test_entry_rejects_unknown_message_dict():
    """裸 dict 直接构造 entry 时未知 role 必须报错（防静默吞掉）。"""
    with pytest.raises(Exception):
        SessionMessageEntry.model_validate(
            {
                "id": "x",
                "message": {"role": "searchResult", "query": "q", "count": 1},
            }
        )
