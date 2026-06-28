"""
ToolLoader / DynamicTool 加载与执行测试。
"""

import asyncio
from pathlib import Path

import pytest
from nova_agent import AgentToolResult
from nova_ai import TextContent

from nova_harness.core.resources.loaders.tools import (
    ToolLoader,
    load_json_file,
    load_text_file,
    load_tool_definition,
)
from nova_harness.core.types.tools import DynamicTool, ToolDefinition


@pytest.fixture
def tool_dir(tmp_path: Path) -> Path:
    """创建一个有效的工具目录（schema.json + executor.py）。"""
    d = tmp_path / "tools" / "hello"
    d.mkdir(parents=True)
    (d / "schema.json").write_text(
        """
{
    "name": "hello",
    "description": "Say hello",
    "parameters": {"type": "object", "properties": {"name": {"type": "string"}}},
    "prompt_snippet": "hello: greet the user",
    "prompt_guidelines": ["Be polite."]
}
""",
        encoding="utf-8",
    )
    (d / "executor.py").write_text(
        """
class ToolExecutor:
    def execute(self, tool_call_id, params, signal, on_update):
        return f"Hello, {params.get('name', 'world')}!"
""",
        encoding="utf-8",
    )
    return d


def test_load_text_file_missing_returns_none(tmp_path: Path):
    assert load_text_file(str(tmp_path / "not_found.txt")) is None


def test_load_text_file_empty_returns_none(tmp_path: Path):
    path = tmp_path / "empty.txt"
    path.write_text("   \n  ", encoding="utf-8")
    assert load_text_file(str(path)) is None


def test_load_json_file_invalid_returns_none(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    assert load_json_file(str(path)) is None


def test_load_tool_definition_reads_snippet_and_guidelines(tool_dir: Path):
    definition = load_tool_definition(str(tool_dir))
    assert definition is not None
    assert definition.name == "hello"
    assert definition.description == "Say hello"
    assert definition.prompt_snippet == "hello: greet the user"
    assert definition.prompt_guidelines == ["Be polite."]
    assert definition.executor_path == str(tool_dir / "executor.py")
    assert definition.tool_dir == str(tool_dir)


def test_load_tool_definition_missing_name_returns_none(tmp_path: Path):
    d = tmp_path / "bad_tool"
    d.mkdir()
    (d / "schema.json").write_text('{"description": "no name"}', encoding="utf-8")
    assert load_tool_definition(str(d)) is None


def test_load_tool_definition_no_executor_returns_none(tmp_path: Path):
    d = tmp_path / "schema_only"
    d.mkdir()
    (d / "schema.json").write_text(
        '{"name": "schema_only", "description": "x"}', encoding="utf-8"
    )
    assert load_tool_definition(str(d)) is not None
    # _load_tool_from_dir 会因为没有 executor 返回 None
    from nova_harness.core.resources.loaders.tools import _load_tool_from_dir

    assert _load_tool_from_dir(str(d)) is None


@pytest.mark.asyncio
async def test_dynamic_tool_executes_and_wraps_string(tool_dir: Path):
    definition = load_tool_definition(str(tool_dir))
    from nova_harness.core.resources.loaders.tools import _load_executor

    executor = _load_executor(definition.executor_path, definition.name)
    definition.execute = executor.execute

    tool = DynamicTool(definition)
    assert tool.name == "hello"
    assert tool.label == "hello"
    result = await tool.execute("tc-1", {"name": "Alice"})
    assert isinstance(result, AgentToolResult)
    assert len(result.content) == 1
    assert result.content[0].text == "Hello, Alice!"


@pytest.mark.asyncio
async def test_dynamic_tool_returns_agent_tool_result_directly():
    definition = ToolDefinition(
        name="raw",
        description="raw result",
        parameters={},
        execute=lambda _tid, _params, _signal, _update: AgentToolResult(
            content=[TextContent(text="raw")], details={"ok": True}
        ),
    )
    tool = DynamicTool(definition)
    result = await tool.execute("tc-2", {})
    assert result.content[0].text == "raw"
    assert result.details == {"ok": True}


@pytest.mark.asyncio
async def test_dynamic_tool_async_execute():
    async def async_execute(_tid, _params, _signal, _update):
        await asyncio.sleep(0)
        return "async result"

    definition = ToolDefinition(
        name="async", description="async", parameters={}, execute=async_execute
    )
    result = await DynamicTool(definition).execute("tc-3", {})
    assert result.content[0].text == "async result"


@pytest.mark.asyncio
async def test_dynamic_tool_missing_execute_handler():
    definition = ToolDefinition(name="missing", description="missing", parameters={})
    result = await DynamicTool(definition).execute("tc-4", {})
    assert result.content[0].text == "Tool 'missing' has no execute handler"


def test_tool_loader_discovers_and_loads(tmp_path: Path):
    # 项目级工具
    project = tmp_path / "project"
    project.mkdir()
    project_tool = project / ".nova" / "tools" / "hello"
    project_tool.mkdir(parents=True)
    (project_tool / "schema.json").write_text(
        '{"name": "hello", "description": "project"}', encoding="utf-8"
    )
    (project_tool / "executor.py").write_text(
        "class ToolExecutor:\n    def execute(self, *a, **k): return 'project'\n",
        encoding="utf-8",
    )

    # 额外目录中的同名工具，应覆盖项目级工具并产生诊断
    extra = tmp_path / "extra" / "hello"
    extra.mkdir(parents=True)
    (extra / "schema.json").write_text(
        '{"name": "hello", "description": "override"}', encoding="utf-8"
    )
    (extra / "executor.py").write_text(
        "class ToolExecutor:\n    def execute(self, *a, **k): return 'override'\n",
        encoding="utf-8",
    )

    loader = ToolLoader(
        agent_dir=str(tmp_path / "agent"),
        cwd=str(project),
        additional_paths=[str(tmp_path / "extra")],
    )
    tools = loader.load_tools()
    assert "hello" in tools
    diagnostics = loader.get_diagnostics()
    assert any("overrides" in d.message for d in diagnostics)


def test_tool_loader_no_tools_flag():
    loader = ToolLoader(no_tools=True)
    assert loader.load_tools() == {}
