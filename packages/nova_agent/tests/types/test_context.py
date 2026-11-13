"""AgentLoopConfig 不可变值对象与 replace 更新契约。"""

import dataclasses

import pytest
from nova_ai import SimpleStreamOptions

from nova_agent import AgentLoopConfig


def test_agent_loop_config_is_frozen(dummy_model):
    config = AgentLoopConfig(stream_options=SimpleStreamOptions(), model=dummy_model)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(config, "tool_execution", "sequential")

    replaced = dataclasses.replace(config, tool_execution="sequential")
    assert replaced.tool_execution == "sequential"
    assert config.tool_execution == "parallel"
