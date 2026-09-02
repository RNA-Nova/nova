"""
Nova Harness 冒烟与集成测试。

- 单元测试：验证 harness 类型可序列化/反序列化。
- 集成测试：调用真实模型完成一次简单对话（需 VOLCENGINE_API_KEY）。
"""

import os
import tempfile
from pathlib import Path

import pytest
from nova_ai.types.base_model import NovaBaseModel
from nova_harness import create_agent_session


class _SampleModel(NovaBaseModel):
    name: str = ""
    count: int = 0


def test_harness_base_model_serialization():
    """NovaBaseModel 提供兼容 mashumaro 的 to_dict / from_dict 别名。"""
    obj = _SampleModel(name="test", count=42)
    data = obj.model_dump()
    assert data == {"name": "test", "count": 42}

    restored = _SampleModel.model_validate(data)
    assert restored.name == "test"
    assert restored.count == 42

    json_data = obj.model_dump_json()
    restored_from_json = _SampleModel.model_validate_json(json_data)
    assert restored_from_json.name == "test"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_agent_session_and_prompt():
    """创建 AgentSession 并发起一次真实 LLM 调用，校验返回成功。"""
    if not os.environ.get("VOLCENGINE_API_KEY"):
        pytest.skip("VOLCENGINE_API_KEY not set")

    with tempfile.TemporaryDirectory() as tmp:
        agent_dir = Path(tmp) / "agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        os.environ["NOVA_AGENT_DIR"] = str(agent_dir)

        runtime = await create_agent_session()
        assert runtime is not None
        session = runtime.session
        assert session is not None
        assert session.agent is not None

        await session.prompt("你好，请用一句话介绍自己。")
        await session.agent.wait_for_idle()

        messages = session.agent.state.messages
        assert len(messages) >= 2
        assert messages[0].role == "user"

        last = messages[-1]
        assert last.role == "assistant"
        assert last.stop_reason != "error"
        assert not last.error_message

        text_parts = [
            c.text
            for c in last.content
            if getattr(c, "type", None) == "text" and getattr(c, "text", None)
        ]
        assert text_parts, "真实模型调用应返回非空文本内容"
