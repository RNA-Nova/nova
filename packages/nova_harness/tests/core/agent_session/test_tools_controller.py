"""ToolController.refresh_registry 回归测试。

回归：启动路径（``refresh_registry``）此前只刷新 ToolsManager 注册表，
不把激活工具同步进 ``Agent.state.tools``——LLM 因此收不到工具定义，
真实运行中模型只能"口述"工具调用（从不产生 tool_execution 事件）。
"""

from types import SimpleNamespace
from typing import Any, List, Optional

from nova_harness.core.agent_session.controllers.tools import ToolController


class _FakeToolsManager:
    """记录调用、持有一个假工具的最小 ToolsManager 替身。"""

    def __init__(self, tool: Any) -> None:
        self._tool = tool
        self.refresh_calls: List[Any] = []
        # _tools_manager() 会按 session 配置回写这些属性
        self.extension_runner = None
        self.base_tools_override = None
        self.custom_tools = None
        self.allowed_tool_names = None
        self.excluded_tool_names = None

    def refresh(
        self, active_tool_names: Optional[List[str]], context_provider: Any
    ) -> None:
        self.refresh_calls.append(active_tool_names)

    def get_active_tools(self) -> List[str]:
        return ["bash"]

    def get_tool(self, name: str) -> Optional[Any]:
        return self._tool if name == "bash" else None


def _make_session(manager: Any) -> Any:
    return SimpleNamespace(
        tools_manager=manager,
        extension_runner=None,
        base_tools_override=None,
        custom_tools=None,
        allowed_tool_names=None,
        excluded_tool_names=None,
        initial_active_tool_names=None,
        get_tool_exec_context=lambda: None,
        agent=SimpleNamespace(state=SimpleNamespace(tools=[])),
    )


def test_refresh_registry_syncs_tools_into_agent_state():
    fake_tool = SimpleNamespace(name="bash")
    manager = _FakeToolsManager(fake_tool)
    session = _make_session(manager)

    ToolController(session).refresh_registry()

    # 激活工具必须出现在 Agent.state.tools（LLM 的工具定义来源）
    assert session.agent.state.tools == [fake_tool]
    # 注册表刷新本身也被调用（initial_active_tool_names=None → 三态 None）
    assert manager.refresh_calls == [None]
