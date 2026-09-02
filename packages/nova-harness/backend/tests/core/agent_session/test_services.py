"""
AgentSessionServices 服务集合测试。

验证构造、字段、工厂方法以及 CreateAgentSessionRuntimeResult 行为。
"""

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest
from nova_harness.core.agent_session.services import (
    AgentSessionServices,
    CreateAgentSessionRuntimeResult,
)
from nova_harness.core.config import AuthStorage, SettingsManager
from nova_harness.core.model import ModelRuntime
from nova_harness.core.resources.loader import ResourceLoader
from nova_harness.core.types.session.diagnostics import AgentSessionRuntimeDiagnostic


class _DummyResourceLoader(ResourceLoader):
    """最小可用的 ResourceLoader 实现，避免 DefaultResourceLoader 的 IO。"""

    def __init__(self, agent_names=None):
        self._agent_names = agent_names or []

    async def reload(self) -> None:
        pass

    def extend_resources(self, paths: Any) -> None:
        pass

    def event_bus(self) -> Any:
        return MagicMock()

    def get_agent_names(self) -> list:
        return list(self._agent_names)

    def get_agents(self) -> Dict[str, Any]:
        return {}

    def get_extensions(self) -> Any:
        return MagicMock(extensions=[], diagnostics=[])

    def get_prompts(self) -> Dict[str, Any]:
        return {"prompts": [], "diagnostics": []}

    def get_skills(self) -> Dict[str, Any]:
        return {}

    def get_tools(self) -> Dict[str, Any]:
        return {}

    def get_context_files(self) -> list:
        return []


class _DummySettingsManager(SettingsManager):
    def __init__(self):  # noqa: D107
        pass


class _DummyAuthStorage(AuthStorage):
    def __init__(self):  # noqa: D107
        pass


class _DummyModelRuntime(ModelRuntime):
    def __init__(self):  # noqa: D107
        pass

    async def refresh(self, allow_network=None, signal=None):  # noqa: D102
        return {"aborted": False, "errors": {}}


def test_services_fields(tmp_path):
    """AgentSessionServices 字段可直接构造并正确设置。"""
    services = AgentSessionServices(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        settings_manager=MagicMock(),
        model_runtime=MagicMock(),
        resource_loader=MagicMock(),
        auth_storage=MagicMock(),
        diagnostics=[
            AgentSessionRuntimeDiagnostic(
                type="warning", message="extension load delayed"
            )
        ],
    )
    assert services.cwd == str(tmp_path)
    assert services.agent_dir == str(tmp_path / "agent")
    assert len(services.diagnostics) == 1
    assert services.diagnostics[0].type == "warning"


def test_services_diagnostics_default_empty(tmp_path):
    """diagnostics 默认应为空列表。"""
    services = AgentSessionServices(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        settings_manager=MagicMock(),
        model_runtime=MagicMock(),
        resource_loader=MagicMock(),
        auth_storage=MagicMock(),
    )
    assert services.diagnostics == []


def test_create_agent_session_runtime_result():
    """CreateAgentSessionRuntimeResult 可正确承载 session/services/diagnostics。"""
    session = MagicMock()
    session.session_manager = MagicMock()
    services = MagicMock()
    result = CreateAgentSessionRuntimeResult(
        session=session,
        services=services,
        extensions_result=MagicMock(),
        diagnostics=[
            AgentSessionRuntimeDiagnostic(type="error", message="factory diagnostic")
        ],
        model_fallback_message="fallback",
    )
    assert result.session is session
    assert result.services is services
    assert result.diagnostics[0].message == "factory diagnostic"
    assert result.model_fallback_message == "fallback"


@pytest.mark.asyncio
async def test_services_create_uses_passed_dependencies(tmp_path):
    """create 工厂方法会复用传入的依赖，不会重新创建默认值。"""
    auth_storage = _DummyAuthStorage()
    settings_manager = _DummySettingsManager()
    model_runtime = _DummyModelRuntime()
    resource_loader = _DummyResourceLoader()

    services = await AgentSessionServices.create(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        auth_storage=auth_storage,
        settings_manager=settings_manager,
        model_runtime=model_runtime,
        resource_loader=resource_loader,
    )

    assert services.auth_storage is auth_storage
    assert services.settings_manager is settings_manager
    assert services.model_runtime is model_runtime
    assert services.resource_loader is resource_loader


@pytest.mark.asyncio
async def test_services_create_picks_agent_name_from_loader(tmp_path):
    """create 在未指定 agent_name 时会使用 resource_loader 返回的第一个 agent。"""
    resource_loader = _DummyResourceLoader(agent_names=["custom_agent"])

    services = await AgentSessionServices.create(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        resource_loader=resource_loader,
    )

    assert services.resource_loader is resource_loader


@pytest.mark.asyncio
async def test_services_create_falls_back_to_base_agent(tmp_path):
    """create 在 resource_loader 无 agent 时回退到 base_agent。"""
    resource_loader = _DummyResourceLoader(agent_names=[])

    services = await AgentSessionServices.create(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        resource_loader=resource_loader,
    )

    assert services.resource_loader is resource_loader
    assert services.cwd == str(tmp_path)


@pytest.mark.asyncio
async def test_services_create_forwards_on_progress_to_package_manager(tmp_path):
    """create 的 on_progress 透传给内部 PackageManager（自愈重装进度）。"""
    events = []
    callback = events.append

    services = await AgentSessionServices.create(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        on_progress=callback,
    )

    package_manager = services.resource_loader._package_manager
    assert package_manager._on_progress is callback
