"""
AgentSessionConfig 配置类测试。

验证字段默认值、必填校验以及序列化行为。
"""

from unittest.mock import MagicMock

import pytest

from nova_harness.core.agent_session.options import AgentSessionConfig
from nova_harness.core.types.events import SessionStartEvent


def _construct_config(**overrides):
    """使用 model_construct 构造 AgentSessionConfig，绕过类型校验。"""
    defaults = {
        "agent": MagicMock(),
        "session_manager": MagicMock(),
        "settings_manager": MagicMock(),
        "cwd": "/tmp",
        "system_prompt_manager": MagicMock(),
        "resource_loader": MagicMock(),
        "model_registry": MagicMock(),
        "scoped_models": [],
        "initial_active_tool_names": ["read", "bash"],
        "base_tools_override": None,
        "extension_runner_ref": None,
        "services": None,
        "session_start_event": None,
    }
    defaults.update(overrides)
    return AgentSessionConfig.model_construct(**defaults)


def test_config_default_initial_active_tools():
    """默认 initial_active_tool_names 包含基础本地工具。"""
    config = AgentSessionConfig.model_construct(
        agent=MagicMock(),
        session_manager=MagicMock(),
        settings_manager=MagicMock(),
        cwd="/tmp",
        system_prompt_manager=MagicMock(),
        resource_loader=MagicMock(),
        model_registry=MagicMock(),
    )
    assert config.initial_active_tool_names == ["read", "bash", "edit", "write"]


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


def test_config_services_excluded_from_serialization():
    """services 与 session_start_event 应被 exclude。"""
    config = _construct_config(
        services=MagicMock(),
        session_start_event=SessionStartEvent(reason="new"),
    )
    dumped = config.model_dump()
    assert "services" not in dumped
    assert "session_start_event" not in dumped


def test_config_cwd_cannot_be_empty():
    """cwd 为空字符串时校验应触发 ValueError。"""
    config = AgentSessionConfig.model_construct(
        agent=MagicMock(),
        session_manager=MagicMock(),
        settings_manager=MagicMock(),
        cwd="",
        system_prompt_manager=MagicMock(),
        resource_loader=MagicMock(),
        model_registry=MagicMock(),
    )
    with pytest.raises(ValueError, match="cwd"):
        config.validate_required()


def test_config_agent_cannot_be_none():
    """agent 为 None 时校验应触发 ValueError。"""
    config = AgentSessionConfig.model_construct(
        agent=None,
        session_manager=MagicMock(),
        settings_manager=MagicMock(),
        cwd="/tmp",
        system_prompt_manager=MagicMock(),
        resource_loader=MagicMock(),
        model_registry=MagicMock(),
    )
    with pytest.raises(ValueError, match="agent"):
        config.validate_required()
