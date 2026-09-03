"""包级用户工具的会话接线端到端测试。

链路：已安装包（settings 声明 path 源）→ PackageResolver 解析 user_tools
类目 → DefaultResourceLoader 装载 → AgentSession 按 agent 白名单注册 →
invoke_user_tool 端到端可用。框架自身不内置任何用户工具。
"""

import json
from pathlib import Path
from typing import Any, List, Optional

import pytest
from nova_agent import CustomAgentMessage
from nova_harness.core.agent_session.services import AgentSessionServices
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.harness.session.message_types import (
    clear_session_message_types,
)
from nova_harness.core.package import PackageManager
from nova_harness.core.resources.loader import DefaultResourceLoader
from nova_harness.core.sdk import create_agent_session_from_services
from nova_harness.core.types.config.settings import PackageSourceSpec, Settings
from nova_harness.core.types.resources.loader import DefaultResourceLoaderOptions
from nova_harness.core.types.session.config import CreateAgentSessionOptions

_EXECUTOR = """
from typing import Literal
from nova_agent import CustomAgentMessage


class FakeResultMessage(CustomAgentMessage):
    text: str = ""
    timestamp: int = 0
    exclude_from_context: bool = False
    role: Literal["fakeResult"] = "fakeResult"

    def to_context_text(self) -> str:
        return self.text


class UserTool:
    name = "fake"
    description = "fake user tool"
    parameters = {"type": "object", "properties": {}}
    MESSAGE_TYPES = [FakeResultMessage]

    def __init__(self, session):
        self._session = session

    async def execute(self, params, on_event, signal):
        return FakeResultMessage(text=f"ran:{params.get('command', '')}", timestamp=1)
"""


class _FakeSettingsManager:
    def __init__(self, global_settings: Settings) -> None:
        self._global = global_settings
        self._project_trusted = True

    def is_project_trusted(self) -> bool:
        return self._project_trusted

    def set_project_trusted(self, value: bool) -> None:
        self._project_trusted = value

    def reload(self) -> None:
        pass

    def get_global_settings(self) -> Settings:
        return self._global

    def get_project_settings(self) -> Settings:
        return Settings()

    def get_package_sources(
        self, local: bool = False, base_dir: Optional[str] = None
    ) -> list:
        from nova_harness.core.package.source.spec import (
            resolve_package_source_from_settings,
        )

        return [
            resolve_package_source_from_settings(s, base_dir or "")
            for s in (self._global.packages or [])
        ]


def _write_user_tool_package(pkg_dir: Path) -> None:
    """写一个含 user_tools 类目的最小包。"""
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "pyproject.toml").write_text(
        '[project]\nname = "fake-st-pkg"\nversion = "1.0.0"\n'
        '[tool.nova]\nuser_tools = ["./user_tools/fake"]\n'
    )
    tool_dir = pkg_dir / "user_tools" / "fake"
    tool_dir.mkdir(parents=True)
    (tool_dir / "executor.py").write_text(_EXECUTOR, encoding="utf-8")


def _write_agent(
    agents_dir: Path, name: str, user_tools: Optional[List[str]] = None
) -> None:
    """写一个 agent 组合声明文件（``agents/<name>.yaml``，name 取文件名 stem）。"""
    agents_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"description: {name} desc"]
    if user_tools is not None:
        lines.append("user_tools:")
        lines.extend(f"  - {t}" for t in user_tools)
    (agents_dir / f"{name}.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def user_tool_env(tmp_path: Path):
    """tmp 项目：.nova/agents 两个 agent + settings 声明一个含用户工具的包。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    cwd.mkdir(parents=True)
    agent_dir.mkdir(parents=True)

    pkg_dir = tmp_path / "pkgs" / "fake-st"
    _write_user_tool_package(pkg_dir)

    _write_agent(cwd / ".nova" / "agents", "allow_all")
    _write_agent(cwd / ".nova" / "agents", "whitelisted", user_tools=["fake"])
    _write_agent(cwd / ".nova" / "agents", "excluded", user_tools=["other"])

    settings = Settings(packages=[{"source": str(pkg_dir)}])
    settings_manager = _FakeSettingsManager(settings)

    package_manager = PackageManager(
        agent_dir=str(agent_dir),
        cwd=str(cwd),
        settings_manager=settings_manager,
    )
    loader = DefaultResourceLoader(
        DefaultResourceLoaderOptions(
            cwd=str(cwd),
            agent_dir=str(agent_dir),
            settings_manager=settings_manager,
            package_manager=package_manager,
        )
    )
    return cwd, agent_dir, loader


@pytest.fixture(autouse=True)
def clean_registry():
    clear_session_message_types()
    yield
    clear_session_message_types()


async def _create_session(cwd, agent_dir, loader, agent_name: str):
    import asyncio

    await loader.reload()
    services = await AgentSessionServices.create(
        cwd=str(cwd),
        agent_dir=str(agent_dir),
        resource_loader=loader,
        project_trusted=True,
    )
    session_manager = SessionManager.in_memory(str(cwd))
    return await create_agent_session_from_services(
        services, session_manager, CreateAgentSessionOptions(agent_name=agent_name)
    )


@pytest.mark.asyncio
async def test_user_tool_registered_and_invokable(user_tool_env):
    """包级用户工具经完整链路注册，invoke 端到端可用。"""
    cwd, agent_dir, loader = user_tool_env
    result = await _create_session(cwd, agent_dir, loader, "whitelisted")
    session = result.session

    catalog = session.list_user_tools()
    assert [t.name for t in catalog] == ["fake"]

    message = await session.invoke_user_tool("fake", {"command": "ls"})
    assert isinstance(message, CustomAgentMessage)
    assert message.role == "fakeResult"
    assert message.text == "ran:ls"


@pytest.mark.asyncio
async def test_user_tool_whitelist_excludes_unlisted(user_tool_env):
    """agent.yaml 声明了白名单但未包含该工具时，工具不注册。"""
    cwd, agent_dir, loader = user_tool_env
    result = await _create_session(cwd, agent_dir, loader, "excluded")
    assert result.session.list_user_tools() == []


@pytest.mark.asyncio
async def test_user_tool_no_whitelist_allows_all(user_tool_env):
    """agent 未声明 user_tools 白名单时允许全部（与 extensions 语义一致）。"""
    cwd, agent_dir, loader = user_tool_env
    result = await _create_session(cwd, agent_dir, loader, "allow_all")
    assert [t.name for t in result.session.list_user_tools()] == ["fake"]


@pytest.mark.asyncio
async def test_change_agent_rebuilds_user_tools(user_tool_env):
    """change_agent 后用户工具注册表按新 agent 白名单重建。"""
    cwd, agent_dir, loader = user_tool_env
    result = await _create_session(cwd, agent_dir, loader, "whitelisted")
    session = result.session
    assert [t.name for t in session.list_user_tools()] == ["fake"]

    await session.change_agent("excluded")
    assert session.list_user_tools() == []

    await session.change_agent("allow_all")
    assert [t.name for t in session.list_user_tools()] == ["fake"]
