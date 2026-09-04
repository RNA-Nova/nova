"""bash 用户工具（``user_tools/bash.py``）测试：UserTool 类端到端与
message_from_result 转换（引擎本体测试见
``tests/nova_coding_agent/bash/test_engine.py``）。
"""

import importlib.util
from pathlib import Path

import pytest

_RESOURCE_EXECUTOR = Path(__file__).parent.parent.parent / "user_tools" / "bash.py"


def _load_user_tool_class():
    """从资源目录加载 UserTool 类（与 loader 同款 import 路径）。"""
    spec = importlib.util.spec_from_file_location(
        "_test_user_tool_bash", _RESOURCE_EXECUTOR
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.UserTool


# ---------------------------------------------------------------------------
# 工厂：create_user_tool（fake session 端到端）
# ---------------------------------------------------------------------------


class _FakeSettingsManager:
    def get_shell_command_prefix(self):
        return None

    def get_shell_path(self):
        return None


class _FakeSessionManager:
    def __init__(self, cwd: str):
        self._cwd = cwd

    def get_cwd(self):
        return self._cwd


class _FakeSession:
    def __init__(self, cwd: str):
        self.settings_manager = _FakeSettingsManager()
        self.session_manager = _FakeSessionManager(cwd)
        self.extension_runner = None


@pytest.mark.asyncio
async def test_bash_user_tool_end_to_end(tmp_path: Path):
    """UserTool 按会话实例化后可直接 execute，返回 BashExecutionMessage。"""
    from nova_coding_agent.bash.message import BashExecutionMessage

    tool = _load_user_tool_class()(_FakeSession(str(tmp_path)))
    assert tool.name == "bash"
    assert "command" in tool.parameters["properties"]

    events = []
    message = await tool.execute(
        {"command": "echo e2e"},
        lambda e, d: events.append((e, d)),
        None,
    )
    assert isinstance(message, BashExecutionMessage)
    assert message.command == "echo e2e"
    assert "e2e" in message.output
    assert message.exit_code == 0
    assert message.exclude_from_context is False
    # start 事件先于一切输出上线：命令串在执行开始前即达前端
    # （慢命令输出先于命令出现的实证缺陷——`$ command` 头应即刻渲染）
    assert events[0] == ("start", {"command": "echo e2e", "excludeFromContext": False})
    # 至少推过一次输出事件
    assert any(e == "output" and "e2e" in d.get("chunk", "") for e, d in events)
    # to_context_text 多态可用
    assert "Ran `echo e2e`" in message.to_context_text()


@pytest.mark.asyncio
async def test_bash_user_tool_exclude_from_context(tmp_path: Path):
    tool = _load_user_tool_class()(_FakeSession(str(tmp_path)))
    message = await tool.execute(
        {"command": "true", "exclude_from_context": True}, None, None
    )
    assert message.exclude_from_context is True


# ---------------------------------------------------------------------------
# message_from_result：user_bash 拦截结果 → 消息转换
# ---------------------------------------------------------------------------


def test_message_from_result_with_bash_result(tmp_path: Path):
    """本地引擎的 BashResult 数据类形态（execute 末尾复用同一转换）。"""
    from nova_coding_agent.bash.engine import BashResult
    from nova_coding_agent.bash.message import BashExecutionMessage

    tool = _load_user_tool_class()(_FakeSession(str(tmp_path)))
    result = BashResult(
        output="out",
        exit_code=3,
        cancelled=False,
        truncated=True,
        full_output_path="/tmp/full.log",
    )
    message = tool.message_from_result(
        {"command": "ls", "exclude_from_context": True}, result
    )
    assert isinstance(message, BashExecutionMessage)
    assert message.command == "ls"
    assert message.output == "out"
    assert message.exit_code == 3
    assert message.cancelled is False
    assert message.truncated is True
    assert message.full_output_path == "/tmp/full.log"
    assert message.exclude_from_context is True
    assert message.timestamp > 0


def test_message_from_result_with_pi_style_dict(tmp_path: Path):
    """扩展返回的驼峰键 dict 形态同样可翻译。"""
    tool = _load_user_tool_class()(_FakeSession(str(tmp_path)))
    message = tool.message_from_result(
        {"command": "remote-cmd"},
        {
            "output": "remote-out",
            "exitCode": 0,
            "cancelled": False,
            "truncated": False,
            "fullOutputPath": None,
        },
    )
    assert message.command == "remote-cmd"
    assert message.output == "remote-out"
    assert message.exit_code == 0
    assert message.cancelled is False
    assert message.truncated is False
    assert message.full_output_path is None
    assert message.exclude_from_context is False


def test_message_from_result_cancelled_has_no_exit_code(tmp_path: Path):
    """取消的结果 exit_code 为 None。"""
    tool = _load_user_tool_class()(_FakeSession(str(tmp_path)))
    message = tool.message_from_result({"command": "sleep 10"}, {"cancelled": True})
    assert message.cancelled is True
    assert message.exit_code is None
    assert message.output == ""
