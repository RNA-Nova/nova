"""默认 stream_fn 注册点的装配与失败契约。"""

import pytest
from _helpers.agent import final_stream
from _helpers.messages import assistant_message, text_of
from nova_ai import SimpleStreamOptions
from nova_protocol import Context, Model

from nova_agent import Agent, get_default_stream_fn, set_default_stream_fn


@pytest.fixture
def clean_default_stream_fn():
    set_default_stream_fn(None)
    yield
    set_default_stream_fn(None)


@pytest.mark.asyncio
async def test_set_default_stream_fn_used_when_stream_fn_omitted(
    dummy_model, clean_default_stream_fn
):
    calls = []

    def default_fn(model: Model, context: Context, options: SimpleStreamOptions):
        calls.append((model, context, options))
        return final_stream(model, "via default")

    set_default_stream_fn(default_fn)
    agent = Agent(stream_fn=None)
    agent.set_model(dummy_model)
    await agent.prompt("run")

    assert len(calls) == 1
    assert text_of(assistant_message(agent.state.messages[-1])) == "via default"


def test_get_default_stream_fn_raises_when_unregistered(clean_default_stream_fn):
    with pytest.raises(RuntimeError, match="No default stream function"):
        get_default_stream_fn()
    with pytest.raises(RuntimeError, match="No default stream function"):
        Agent()
