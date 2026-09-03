"""entry_appended 事件测试：扩展 append_entry 追加自定义条目后实时上 Bus 2。

对齐 pi：仅 custom 条目有此事件（消息/压缩等各有专属通道）——
前端 mapping 据此把扩展自定义内容实时放进 transcript。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from nova_harness.core import AgentSession
from nova_harness.core.types.events import EntryAppendedEvent
from nova_harness.core.types.session.config import AgentSessionConfig


def _make_config() -> AgentSessionConfig:
    agent = MagicMock()
    agent.state.messages = []
    agent.state.is_streaming = False
    return AgentSessionConfig(
        agent=agent,
        session_manager=MagicMock(),
        settings_manager=MagicMock(),
        cwd="/tmp",
        system_prompt_manager=MagicMock(),
        tools_manager=MagicMock(),
        resource_loader=MagicMock(),
        model_runtime=MagicMock(),
        scoped_models=[],
        initial_active_tool_names=[],
        base_tools_override=None,
        extension_runner_ref=None,
        session_start_event=None,
    )


@pytest.mark.asyncio
async def test_append_entry_emits_entry_appended_with_entry():
    session = AgentSession(_make_config())

    entry = SimpleNamespace(
        id="e1", type="custom", custom_type="git_status", data={"branch": "main"}
    )
    session.session_manager.append_custom_entry.return_value = "e1"
    session.session_manager.get_entry.return_value = entry

    received = []
    session.subscribe(lambda event, *args: received.append(event))

    captured = {}

    class FakeRunner:
        def bind_core(self, actions, context_actions, provider_actions):
            captured["actions"] = actions

        def emit_error(self, *args, **kwargs):
            pass

    session._bind_extension_core(FakeRunner())
    entry_id = captured["actions"].append_entry("git_status", {"branch": "main"})

    assert entry_id == "e1"
    session.session_manager.append_custom_entry.assert_called_once_with(
        "git_status", {"branch": "main"}
    )
    appended = [e for e in received if isinstance(e, EntryAppendedEvent)]
    assert len(appended) == 1
    assert appended[0].entry is entry
    assert appended[0].entry.custom_type == "git_status"


@pytest.mark.asyncio
async def test_append_entry_no_event_when_entry_missing():
    """条目取不到（防御）：不发射事件，但仍返回 entry_id。"""
    session = AgentSession(_make_config())
    session.session_manager.append_custom_entry.return_value = "ghost"
    session.session_manager.get_entry.return_value = None

    received = []
    session.subscribe(lambda event, *args: received.append(event))

    captured = {}

    class FakeRunner:
        def bind_core(self, actions, context_actions, provider_actions):
            captured["actions"] = actions

        def emit_error(self, *args, **kwargs):
            pass

    session._bind_extension_core(FakeRunner())
    entry_id = captured["actions"].append_entry("t", None)

    assert entry_id == "ghost"
    assert not [e for e in received if isinstance(e, EntryAppendedEvent)]
