"""转录折叠机测试（对齐 pi utils/transcript.ts 语义）。

normalize → resolve 两形态（穿过/折叠）；折叠机三件（tools/system/text）。
"""

from nova_ai.api_impls._shared.transcript import (
    collapse_system_messages,
    get_current_system_message,
    get_current_tools,
    get_initial_system_message,
    get_system_message_text,
    normalize_context,
    render_system_message_update,
    resolve_transcript,
    without_initial_system_message,
)
from nova_protocol import (
    AssistantMessage,
    Context,
    KnownApi,
    Model,
    ModelCost,
    StopReason,
    SystemMessage,
    Tool,
    ToolReference,
    Usage,
    UserMessage,
)


def _tool(name: str) -> Tool:
    return Tool(name=name, description=f"{name} d", parameters={"type": "object"})


def _ctx(system_prompt=None, tools=None, messages=None) -> Context:
    return Context(
        system_prompt=system_prompt,
        tools=tools,
        messages=list(messages or []),
    )


class TestNormalize:
    def test_prompt_and_tools_folded_to_leading_message(self):
        normalized = normalize_context(
            _ctx(system_prompt="你是助手", tools=[_tool("bash")], messages=[])
        )
        assert len(normalized) == 1
        head = get_initial_system_message(normalized)
        assert head is not None
        assert head.content == "你是助手"
        tools_added = head.tools_added
        assert tools_added is not None
        assert [t.name for t in tools_added] == ["bash"]

    def test_empty_context_stays_empty(self):
        assert normalize_context(_ctx()) == []


class TestFold:
    def test_get_current_tools_add_remove_in_order(self):
        messages = [
            SystemMessage(tools_added=[_tool("a"), _tool("b")], timestamp=1),
            SystemMessage(tools_removed=[ToolReference(name="a")], timestamp=2),
            SystemMessage(tools_added=[_tool("c")], timestamp=3),
        ]
        assert [t.name for t in get_current_tools(messages)] == ["b", "c"]

    def test_get_current_system_message_sections_patch(self):
        """sections 按名打补丁：后写覆盖、None 删除（pi 同款）。"""
        folded = get_current_system_message(
            [
                SystemMessage(
                    content="base", sections={"a": "1", "b": "2"}, timestamp=1
                ),
                SystemMessage(sections={"a": "1+", "b": None}, timestamp=2),
            ]
        )
        assert folded is not None
        assert folded.content == "base"
        assert folded.sections == {"a": "1+"}

    def test_collapse_produces_single_leading_message(self):
        collapsed = collapse_system_messages(
            [
                SystemMessage(content="base", timestamp=1),
                UserMessage(content="u"),
                SystemMessage(content="more", timestamp=2),
            ]
        )
        assert [m.role for m in collapsed] == ["system", "user"]
        head = collapsed[0]
        assert isinstance(head, SystemMessage)
        assert get_system_message_text(head) == "base\n\nmore"


class TestResolve:
    def test_passthrough_when_supported(self):
        messages = [SystemMessage(content="s", timestamp=1), UserMessage(content="u")]
        out = resolve_transcript(messages, True)
        assert [m.role for m in out] == ["system", "user"]

    def test_collapse_when_unsupported(self):
        messages = [SystemMessage(content="s", timestamp=1), UserMessage(content="u")]
        out = resolve_transcript(messages, False)
        assert [m.role for m in out] == ["system", "user"]
        assert isinstance(out[0], SystemMessage)
        assert get_system_message_text(out[0]) == "s"

    def test_without_initial(self):
        messages = [SystemMessage(content="s"), UserMessage(content="u")]
        assert [m.role for m in without_initial_system_message(messages)] == ["user"]


class TestRender:
    def test_update_render_names_sections(self):
        msg = SystemMessage(sections={"skills": "x", "tools": None})
        text = render_system_message_update(msg)
        assert 'Updated system prompt section "skills"' in text
        assert 'Removed system prompt section "tools".' in text
