"""SyncSessionResult 出参归一往返保形测试。

回归锚点（resume 崩溃修复）：handler 返回的 dict 经 ``MethodRegistry.dispatch``
出参归一（``SyncSessionResult.model_validate`` + ``dump_wire``）时，item 的
子类字段（text/content/command 等）不得被剥——SerializeAsAny 只管序列化
方向，校验侧必须是可判别联合（``WireItem``：框架变体按 type 判别、包级
dict 落 CustomItem 透传、包级实例经基类成员原样保留）。
"""

from __future__ import annotations

from typing import Literal

from nova_harness.server.protocol.methods.shapes import (
    SessionStateResult,
    SyncSessionResult,
)
from nova_harness.server.types.items import (
    AgentMessageItem,
    NovaItem,
    ThinkingItem,
)


class FakePackageItem(NovaItem):
    """模拟包级 item 变体（type 非框架六型——如 bundle 的 BashExecutionItem）。"""

    type: Literal["fakePackage"] = "fakePackage"
    command: str = ""
    output: str = ""


def _min_state() -> SessionStateResult:
    """最小合法快照（SyncSessionResult.state 的必填字段面）。"""
    return SessionStateResult(
        session_id="s1",
        cwd="/tmp",
        thinking_level="off",
        supports_thinking=False,
        available_thinking_levels=[],
        active_tools=[],
        message_count=0,
        pending_message_count=0,
        steering_messages=[],
        follow_up_messages=[],
        is_streaming=False,
        is_compacting=False,
        is_retrying=False,
        auto_retry_enabled=False,
        auto_compaction_enabled=True,
        steering_mode="one",
        follow_up_mode="one",
    )


def _roundtrip(items: list) -> list:
    """模拟 dispatch 出参归一：handler dump_wire → model_validate → dump_wire。"""
    result = SyncSessionResult(
        state=_min_state(), entries=[], total=0, items=items, total_items=len(items)
    )
    return SyncSessionResult.model_validate(result.dump_wire()).dump_wire()["items"]


class TestWireItemRoundtrip:
    """出参归一（dict 重建）后 item 子类字段完整。"""

    def test_framework_variants_keep_own_fields(self) -> None:
        items = _roundtrip(
            [
                ThinkingItem(id="a:th0", status="done", source="agent", ts=1, text="思考"),
                AgentMessageItem(
                    id="a:t1", status="done", source="agent", ts=1, text="正文"
                ),
            ]
        )
        assert items[0]["text"] == "思考"
        assert items[1]["text"] == "正文"

    def test_all_framework_variant_fields_survive(self) -> None:
        items = _roundtrip(
            [
                {
                    "id": "u1",
                    "type": "userMessage",
                    "status": "done",
                    "source": "user",
                    "ts": 1,
                    "content": [{"type": "text", "text": "你好"}],
                },
                {
                    "id": "t1",
                    "type": "toolCall",
                    "status": "done",
                    "source": "agent",
                    "ts": 1,
                    "tool": "read",
                    "args": {"path": "/a"},
                    "result": {"content": []},
                },
                {
                    "id": "c1",
                    "type": "compaction",
                    "status": "done",
                    "source": "agent",
                    "ts": 1,
                    "summary": "摘要",
                    "tokensBefore": 100,
                },
            ]
        )
        assert items[0]["content"][0]["text"] == "你好"
        assert items[1]["tool"] == "read"
        assert items[1]["args"] == {"path": "/a"}
        assert items[2]["summary"] == "摘要"
        assert items[2]["tokensBefore"] == 100

    def test_package_dict_falls_back_to_custom_item(self) -> None:
        """包级变体 dict（线上已 dump 形态）：落 CustomItem，额外字段透传。"""
        items = _roundtrip(
            [
                {
                    "id": "b1",
                    "type": "bashExecution",
                    "status": "done",
                    "source": "user",
                    "ts": 1,
                    "command": "ls",
                    "output": "a.txt",
                    "exitCode": 0,
                }
            ]
        )
        assert items[0]["type"] == "bashExecution"
        assert items[0]["command"] == "ls"
        assert items[0]["output"] == "a.txt"
        assert items[0]["exitCode"] == 0

    def test_package_instance_passes_through(self) -> None:
        """包级**实例**（handler 直接持有的 NovaItem 子类对象）原样保留，
        dump 时按自身 schema 出全字段。"""
        stub = FakePackageItem(id="b2", command="pwd", output="/tmp")
        items = _roundtrip([stub])
        assert items[0]["command"] == "pwd"
        assert items[0]["output"] == "/tmp"
