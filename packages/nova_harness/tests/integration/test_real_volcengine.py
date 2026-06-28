"""
真实火山引擎 API 集成测试。

这些测试需要环境变量 ``VOLCENGINE_API_KEY``；未设置时自动跳过。
为避免滥用真实 API，所有测试都使用极简提示词并标记为 integration。
"""

import os
import tempfile
from pathlib import Path

import pytest
from nova_ai import (
    VOLCENGINE_MODELS,
    Context,
    TextContent,
    UserMessage,
    complete_simple,
)

from nova_harness import create_agent_session


def _volc_key():
    return os.environ.get("VOLCENGINE_API_KEY")


def _flash_model():
    return VOLCENGINE_MODELS.get("deepseek-v4-flash-260425")


@pytest.fixture
def temp_agent_dir():
    with tempfile.TemporaryDirectory() as tmp:
        agent_dir = Path(tmp) / "agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        old = os.environ.get("NOVA_AGENT_DIR")
        os.environ["NOVA_AGENT_DIR"] = str(agent_dir)
        yield str(agent_dir)
        if old is None:
            os.environ.pop("NOVA_AGENT_DIR", None)
        else:
            os.environ["NOVA_AGENT_DIR"] = old


@pytest.mark.integration
@pytest.mark.asyncio
async def test_complete_simple_with_volcengine():
    """直接调用 nova_ai complete_simple，验证火山引擎连通性。"""
    key = _volc_key()
    if not key:
        pytest.skip("VOLCENGINE_API_KEY not set")

    model = _flash_model()
    if model is None:
        pytest.skip("deepseek-v4-flash-260425 not available")

    os.environ["VOLCENGINE_API_KEY"] = key
    context = Context(
        system_prompt="You are a helpful assistant.",
        messages=[
            UserMessage(
                role="user", content=[TextContent(text="Say 'pong' and nothing else.")]
            )
        ],
    )
    result = await complete_simple(model, context)
    assert result.stop_reason != "error"
    assert result.error_message is None or result.error_message == ""
    text = "".join(c.text for c in result.content if getattr(c, "type", None) == "text")
    assert "pong" in text.lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_agent_session_and_prompt_volcengine(temp_agent_dir):
    """通过 harness 创建真实会话并发送一条消息。"""
    key = _volc_key()
    if not key:
        pytest.skip("VOLCENGINE_API_KEY not set")

    runtime = await create_agent_session()
    session = runtime.session
    assert session is not None
    assert session.model is not None
    assert session.model.provider == "volcengine"

    await session.prompt("Please reply with the single word 'ok'.")
    await session.agent.wait_for_idle()

    messages = session.messages
    assert len(messages) >= 2
    last = messages[-1]
    assert getattr(last, "role", None) == "assistant"
    assert getattr(last, "stop_reason", None) != "error"

    text = "".join(
        getattr(c, "text", "")
        for c in getattr(last, "content", [])
        if getattr(c, "type", None) == "text"
    )
    assert text.strip()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_set_model_with_volcengine(temp_agent_dir):
    """使用真实 key 切换模型。"""
    key = _volc_key()
    if not key:
        pytest.skip("VOLCENGINE_API_KEY not set")

    runtime = await create_agent_session()
    session = runtime.session
    flash = _flash_model()
    if flash is None:
        pytest.skip("flash model not available")

    await session.set_model(flash)
    assert session.model.id == "deepseek-v4-flash-260425"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cycle_model_with_volcengine(temp_agent_dir):
    """真实 key 下 cycle_model 应能切换到下一个可用模型。"""
    key = _volc_key()
    if not key:
        pytest.skip("VOLCENGINE_API_KEY not set")

    runtime = await create_agent_session()
    session = runtime.session
    initial_id = session.model.id
    result = await session.cycle_model("forward")
    if result is not None:
        assert result.model.id != initial_id
