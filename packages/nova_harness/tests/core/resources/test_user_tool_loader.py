"""UserToolLoader 测试：UserTool 类的加载、元数据校验与诊断。"""

from pathlib import Path
from typing import Literal

import pytest
from nova_agent import CustomAgentMessage
from nova_harness.core.harness.session.message_types import (
    clear_session_message_types,
    get_session_message_type,
)
from nova_harness.core.resources.loaders.user_tools import UserToolLoader

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
    description = "测试用户工具"
    parameters = {"type": "object", "properties": {"command": {"type": "string"}}}
    MESSAGE_TYPES = [FakeResultMessage]

    def __init__(self, session):
        self._session = session

    async def execute(self, params, on_event, signal):
        return FakeResultMessage(text=f"ran:{params.get('command', '')}", timestamp=1)
"""


@pytest.fixture(autouse=True)
def clean_registry():
    clear_session_message_types()
    yield
    clear_session_message_types()


def _make_tool_dir(root: Path, name: str, executor_text: str = _EXECUTOR) -> Path:
    tool_dir = root / name
    tool_dir.mkdir(parents=True)
    (tool_dir / "executor.py").write_text(executor_text, encoding="utf-8")
    return tool_dir


def test_load_tool_dir(tmp_path: Path):
    tool_dir = _make_tool_dir(tmp_path, "fake")
    loader = UserToolLoader(additional_paths=[str(tool_dir)])
    resources = loader.load_user_tools()

    assert list(resources) == ["fake"]
    resource = resources["fake"]
    assert resource.description == "测试用户工具"
    assert "command" in resource.parameters["properties"]
    assert loader.get_diagnostics() == []


def test_load_registers_message_types(tmp_path: Path):
    tool_dir = _make_tool_dir(tmp_path, "fake")
    UserToolLoader(additional_paths=[str(tool_dir)]).load_user_tools()
    cls = get_session_message_type("fakeResult")
    assert cls is not None
    assert cls.model_fields["role"].default == "fakeResult"


@pytest.mark.asyncio
async def test_create_binds_session_and_executes(tmp_path: Path):
    tool_dir = _make_tool_dir(tmp_path, "fake")
    resources = UserToolLoader(additional_paths=[str(tool_dir)]).load_user_tools()
    definition = resources["fake"].create(object())
    assert definition.name == "fake"
    assert definition.description == "测试用户工具"
    message = await definition.execute({"command": "ls"}, None, None)
    assert message.text == "ran:ls"


def test_container_dir_scans_children(tmp_path: Path):
    container = tmp_path / "user_tools"
    _make_tool_dir(container, "fake")
    loader = UserToolLoader(additional_paths=[str(container)])
    assert list(loader.load_user_tools()) == ["fake"]


def test_collision_first_wins(tmp_path: Path):
    dir_a = _make_tool_dir(tmp_path / "a", "fake")
    dir_b = _make_tool_dir(tmp_path / "b", "fake")
    loader = UserToolLoader(additional_paths=[str(dir_a), str(dir_b)])
    resources = loader.load_user_tools()
    assert list(resources) == ["fake"]
    collisions = [d for d in loader.get_diagnostics() if d.category == "collision"]
    assert len(collisions) == 1
    assert collisions[0].collision.name == "fake"


def test_import_failure_diagnostic(tmp_path: Path):
    """executor.py 存在但导入失败（语法错误等）→ 诊断，不静默。"""
    _make_tool_dir(tmp_path, "fake", executor_text="def broken(:\n")
    loader = UserToolLoader(additional_paths=[str(tmp_path / "fake")])
    assert loader.load_user_tools() == {}
    assert any("import" in d.message.lower() for d in loader.get_diagnostics())


def test_missing_class_diagnostic(tmp_path: Path):
    _make_tool_dir(tmp_path, "fake", executor_text="X = 1\n")
    loader = UserToolLoader(additional_paths=[str(tmp_path / "fake")])
    assert loader.load_user_tools() == {}
    assert any("UserTool" in d.message for d in loader.get_diagnostics())


def test_missing_name_attribute_diagnostic(tmp_path: Path):
    _make_tool_dir(
        tmp_path,
        "fake",
        executor_text=(
            "class UserTool:\n"
            "    description = 'no name'\n"
            "    parameters = {}\n"
            "    def __init__(self, session): pass\n"
        ),
    )
    loader = UserToolLoader(additional_paths=[str(tmp_path / "fake")])
    assert loader.load_user_tools() == {}
    assert any("'name'" in d.message for d in loader.get_diagnostics())


def test_wrong_parameters_type_diagnostic(tmp_path: Path):
    _make_tool_dir(
        tmp_path,
        "fake",
        executor_text=(
            "class UserTool:\n"
            "    name = 'fake'\n"
            "    description = 'bad'\n"
            "    parameters = 'not-a-dict'\n"
            "    def __init__(self, session): pass\n"
        ),
    )
    loader = UserToolLoader(additional_paths=[str(tmp_path / "fake")])
    assert loader.load_user_tools() == {}
    assert any("parameters" in d.message for d in loader.get_diagnostics())


def test_no_user_tools(tmp_path: Path):
    tool_dir = _make_tool_dir(tmp_path, "fake")
    loader = UserToolLoader(additional_paths=[str(tool_dir)], no_user_tools=True)
    assert loader.load_user_tools() == {}
