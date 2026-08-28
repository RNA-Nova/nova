"""
AgentSessionConfig 配置类测试。

验证字段默认值、必填校验以及内部字段不在 repr 中暴露。
"""

from unittest.mock import MagicMock

import pytest

from nova_harness.core.types.events import SessionStartEvent
from nova_harness.core.types.session.config import AgentSessionConfig


def _construct_config(**overrides):
    """构造 AgentSessionConfig。"""
    defaults = {
        "agent": MagicMock(),
        "session_manager": MagicMock(),
        "settings_manager": MagicMock(),
        "cwd": "/tmp",
        "system_prompt_manager": MagicMock(),
        "resource_loader": MagicMock(),
        "model_runtime": MagicMock(),
        "scoped_models": [],
        "initial_active_tool_names": ["read", "bash"],
        "base_tools_override": None,
        "extension_runner_ref": None,
        "session_start_event": None,
    }
    defaults.update(overrides)
    return AgentSessionConfig(**defaults)


def test_config_default_initial_active_tools():
    """默认 initial_active_tool_names 为 None（三态"未指定"，由 ToolsManager 默认激活全部）。"""
    config = AgentSessionConfig(
        agent=MagicMock(),
        session_manager=MagicMock(),
        settings_manager=MagicMock(),
        cwd="/tmp",
        system_prompt_manager=MagicMock(),
        resource_loader=MagicMock(),
        model_runtime=MagicMock(),
    )
    assert config.initial_active_tool_names is None


def test_config_custom_initial_active_tools():
    """可覆盖初始激活工具白名单。"""
    config = _construct_config(initial_active_tool_names=["read"])
    assert config.initial_active_tool_names == ["read"]


def test_config_scoped_models_default():
    """scoped_models 默认为空列表。"""
    config = _construct_config()
    assert config.scoped_models == []


def test_config_base_tools_override_default():
    """base_tools_override 默认为 None。"""
    config = _construct_config()
    assert config.base_tools_override is None


def test_config_excluded_fields_not_in_repr():
    """session_start_event 与 extension_runner_ref 不在 repr 中暴露。"""
    config = _construct_config(
        session_start_event=SessionStartEvent(reason="new"),
    )
    representation = repr(config)
    assert "session_start_event" not in representation
    assert "extension_runner_ref" not in representation


def test_config_cwd_cannot_be_empty():
    """cwd 为空字符串时校验应触发 ValueError。"""
    with pytest.raises(ValueError, match="cwd"):
        AgentSessionConfig(
            agent=MagicMock(),
            session_manager=MagicMock(),
            settings_manager=MagicMock(),
            cwd="",
            system_prompt_manager=MagicMock(),
            resource_loader=MagicMock(),
            model_runtime=MagicMock(),
        )


def test_config_agent_cannot_be_none():
    """agent 为 None 时校验应触发 ValueError。"""
    with pytest.raises(ValueError, match="agent"):
        AgentSessionConfig(
            agent=None,  # type: ignore[arg-type]
            session_manager=MagicMock(),
            settings_manager=MagicMock(),
            cwd="/tmp",
            system_prompt_manager=MagicMock(),
            resource_loader=MagicMock(),
            model_runtime=MagicMock(),
        )
