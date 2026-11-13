"""工具执行批次值对象的不可变契约。"""

import dataclasses

import pytest

from nova_agent.types.tool_execution import ExecutedToolCallBatch


def test_tool_call_batch_is_frozen():
    batch = ExecutedToolCallBatch()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(batch, "terminate", True)
