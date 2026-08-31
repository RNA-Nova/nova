"""
ToolLoader / DynamicTool 加载与执行测试（类属性元数据 + ToolContext 注入）。

覆盖：
- 目录形态（``<name>/executor.py``）与单文件形态（``<name>.py``）；
- 构造期 ToolContext 注入（cwd/settings 不变量）；
- 执行期 ToolExecContext 经 context_provider 注入（execute 第 5 参）；
- label / execution_mode / prepare_arguments 类属性映射。

加载器只产出 ``ToolDefinition``；包装为 ``DynamicTool`` 并注入
``context_provider`` 统一发生在 ``ToolsManager.refresh``。
"""

import asyncio
from pathlib import Path

import pytest
from nova_agent import AgentToolResult
from nova_ai import TextContent

from nova_harness.core.harness.tools.dynamic_tool import DynamicTool
from nova_harness.core.resources.loaders.tools import ToolLoader, _load_tool
from nova_harness.core.types.resources.tools import (
    NULL_TOOL_SETTINGS,
    ToolContext,
    ToolDefinition,
    ToolExecContext,
)

_EXECUTOR = """
class Tool:
    name = "hello"
    description = "Say hello"
    parameters = {"type": "object", "properties": {"name": {"type": "string"}}}
    prompt_snippet = "hello: greet the user"
    prompt_guidelines = ["Be polite."]

    def __init__(self, context):
        self.context = context

    def execute(self, tool_call_id, params, signal, on_update, ctx):
        return f"Hello, {params.get('name', 'world')}!"
"""


def _context(cwd: str = "/tmp") -> ToolContext:
    return ToolContext(cwd=cwd, settings=NULL_TOOL_SETTINGS)


@pytest.fixture
def tool_dir(tmp_path: Path) -> Path:
    """创建一个有效的工具目录（仅 executor.py，元数据为类属性）。"""
    d = tmp_path / "tools" / "hello"
    d.mkdir(parents=True)
    (d / "executor.py").write_text(_EXECUTOR, encoding="utf-8")
    return d


@pytest.mark.asyncio
async def test_dynamic_tool_executes_and_wraps_string(tool_dir: Path):
    definition = _load_tool(str(tool_dir), context=_context())
    assert definition is not None
    assert definition.name == "hello"
    tool = DynamicTool(definition)
    # label 缺省时包装层回退为工具名
    assert tool.label == "hello"
    result = await tool.execute("tc-1", {"name": "Alice"})
    assert isinstance(result, AgentToolResult)
    assert len(result.content) == 1
    assert result.content[0].text == "Hello, Alice!"


def test_tool_metadata_from_class_attributes(tool_dir: Path):
    """元数据全部来自 Tool 类属性（无独立元数据文件）。"""
    definition = _load_tool(str(tool_dir), context=_context())
    assert definition is not None
    assert definition.name == "hello"
    assert definition.description == "Say hello"
    assert definition.parameters == {
        "type": "object",
        "properties": {"name": {"type": "string"}},
    }
    assert definition.prompt_snippet == "hello: greet the user"
    assert definition.prompt_guidelines == ["Be polite."]
    assert definition.executor_path == str(tool_dir / "executor.py")
    assert definition.tool_dir == str(tool_dir)


def test_load_tool_missing_executor_returns_none(tmp_path: Path):
    d = tmp_path / "empty_tool"
    d.mkdir()
    diagnostics = []
    assert _load_tool(str(d), context=_context(), diagnostics=diagnostics) is None
    assert any("executor" in dgn.message for dgn in diagnostics)


def test_load_tool_missing_name_attribute_diagnostic(tmp_path: Path):
    d = tmp_path / "bad_tool"
    d.mkdir()
    (d / "executor.py").write_text(
        "class Tool:\n"
        "    description = 'no name'\n"
        "    parameters = {}\n"
        "    def execute(self, *a, **k): return None\n",
        encoding="utf-8",
    )
    diagnostics = []
    assert _load_tool(str(d), context=_context(), diagnostics=diagnostics) is None
    assert any("'name'" in dgn.message for dgn in diagnostics)


def test_load_tool_wrong_parameters_type_diagnostic(tmp_path: Path):
    d = tmp_path / "bad_tool"
    d.mkdir()
    (d / "executor.py").write_text(
        "class Tool:\n"
        "    name = 'bad'\n"
        "    description = 'bad'\n"
        "    parameters = 'not-a-dict'\n"
        "    def execute(self, *a, **k): return None\n",
        encoding="utf-8",
    )
    diagnostics = []
    assert _load_tool(str(d), context=_context(), diagnostics=diagnostics) is None
    assert any("parameters" in dgn.message for dgn in diagnostics)


def test_single_file_form(tmp_path: Path):
    """单文件形态：<name>.py 本身即工具。"""
    f = tmp_path / "hello.py"
    f.write_text(_EXECUTOR, encoding="utf-8")
    definition = _load_tool(str(f), context=_context())
    assert definition is not None
    assert definition.name == "hello"
    assert definition.executor_path == str(f)
    assert definition.tool_dir == str(tmp_path)


def test_single_file_via_loader_additional_paths(tmp_path: Path):
    """additional_paths 直接指向 .py 文件时可加载。"""
    f = tmp_path / "hello.py"
    f.write_text(_EXECUTOR, encoding="utf-8")
    loader = ToolLoader(additional_paths=[str(f)])
    tools = loader.load_tools()
    assert "hello" in tools


def test_context_injected_into_executor(tool_dir: Path):
    """Tool 构造期收到 ToolContext（cwd/settings 不变量）。"""
    ctx = _context(cwd="/work/project")
    definition = _load_tool(str(tool_dir), context=ctx)
    assert definition is not None
    executor = definition.execute.__self__
    assert executor.context is ctx
    assert executor.context.cwd == "/work/project"


@pytest.mark.asyncio
async def test_exec_context_injected_via_context_provider(tmp_path: Path):
    """执行期 ToolExecContext 经 context_provider 现取并作为 execute 第 5 参注入。"""
    f_code = """
class Tool:
    name = "probe"
    description = "probe ctx"
    parameters = {}
    received = None

    def __init__(self, context):
        pass

    def execute(self, tool_call_id, params, signal, on_update, ctx):
        type(self).received = ctx
        return "ok"
"""
    d = tmp_path / "probe"
    d.mkdir()
    (d / "executor.py").write_text(f_code, encoding="utf-8")
    definition = _load_tool(str(d), context=_context())
    assert definition is not None

    # 无 provider：共享兜底 NULL_TOOL_EXEC_CONTEXT（model=None）
    await DynamicTool(definition).execute("tc-null", {})
    executor = definition.execute.__self__
    assert executor.received is not None
    assert executor.received.model is None

    # 有 provider：每次调用现取当前值（模型切换即刻反映）
    current = {"model": "model-a"}
    tool = DynamicTool(
        definition, context_provider=lambda: ToolExecContext(model=current["model"])
    )
    await tool.execute("tc-a", {})
    assert executor.received.model == "model-a"
    current["model"] = "model-b"
    await tool.execute("tc-b", {})
    assert executor.received.model == "model-b"


def test_optional_class_attributes_mapped(tmp_path: Path):
    """label / execution_mode / prepare_arguments 映射进 ToolDefinition。"""
    f = tmp_path / "fancy.py"
    f.write_text(
        "class Tool:\n"
        "    name = 'fancy'\n"
        "    description = 'fancy tool'\n"
        "    parameters = {}\n"
        "    label = 'Fancy'\n"
        "    execution_mode = 'sequential'\n"
        "    def prepare_arguments(self, args): return args\n"
        "    def __init__(self, context): pass\n"
        "    def execute(self, *a, **k): return 'ok'\n",
        encoding="utf-8",
    )
    definition = _load_tool(str(f), context=_context())
    assert definition is not None
    assert definition.label == "Fancy"
    assert definition.execution_mode == "sequential"
    assert definition.prepare_arguments is not None


@pytest.mark.asyncio
async def test_dynamic_tool_returns_agent_tool_result_directly():
    definition = ToolDefinition(
        name="raw",
        description="raw result",
        parameters={},
        execute=lambda _tid, _params, _signal, _update, _ctx: AgentToolResult(
            content=[TextContent(text="raw")], details={"ok": True}
        ),
    )
    tool = DynamicTool(definition)
    result = await tool.execute("tc-2", {})
    assert result.content[0].text == "raw"
    assert result.details == {"ok": True}


@pytest.mark.asyncio
async def test_dynamic_tool_async_execute():
    async def async_execute(_tid, _params, _signal, _update, _ctx):
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


def _write_tool(directory: Path, name: str, description: str, result: str) -> Path:
    """写一个合法工具目录（executor.py，类属性元数据）。"""
    directory.mkdir(parents=True)
    (directory / "executor.py").write_text(
        f"class Tool:\n"
        f"    name = {name!r}\n"
        f"    description = {description!r}\n"
        f"    parameters = {{'type': 'object', 'properties': {{}}}}\n"
        f"    def __init__(self, context): pass\n"
        f"    def execute(self, *a, **k): return {result!r}\n",
        encoding="utf-8",
    )
    return directory


def test_tool_loader_discovers_and_loads(tmp_path: Path):
    # 工具只能通过 additional_paths 显式提供。
    extra = _write_tool(tmp_path / "extra" / "hello", "hello", "extra", "extra")

    loader = ToolLoader(
        agent_dir=str(tmp_path / "agent"),
        cwd=str(tmp_path / "project"),
        additional_paths=[str(tmp_path / "extra")],
    )
    tools = loader.load_tools()
    assert "hello" in tools


def test_tool_loader_first_wins_on_collision(tmp_path: Path):
    """同名工具碰撞 first-wins：先出现的胜出，后者记录 collision 诊断。"""
    first = _write_tool(tmp_path / "first" / "hello", "hello", "first", "first")
    second = _write_tool(tmp_path / "second" / "hello", "hello", "second", "second")

    loader = ToolLoader(additional_paths=[str(first), str(second)])
    tools = loader.load_tools()

    # first-wins：先出现的工具胜出
    assert tools["hello"].description == "first"

    diagnostics = loader.get_diagnostics()
    collision = next((d for d in diagnostics if d.category == "collision"), None)
    assert collision is not None
    assert "shadowed by" in collision.message
    assert collision.collision is not None
    assert collision.collision.winner_path == str(first)
    assert collision.collision.loser_path == str(second)


def test_tool_loader_allowed_names_filters_tools(tmp_path: Path):
    """allowed_names 预过滤只加载白名单内的工具。"""
    tools_root = tmp_path / "tools"
    for name in ["read", "write", "jump"]:
        _write_tool(tools_root / name, name, name, "ok")

    loader = ToolLoader(
        additional_paths=[str(tools_root)], allowed_names={"read", "jump"}
    )
    tools = loader.load_tools()
    assert set(tools.keys()) == {"read", "jump"}


def test_tool_loader_allowed_names_empty_set_loads_none(tmp_path: Path):
    """allowed_names 为空集合时不加载任何工具。"""
    tools_root = tmp_path / "tools"
    _write_tool(tools_root / "read", "read", "read", "ok")

    loader = ToolLoader(additional_paths=[str(tools_root)], allowed_names=set())
    assert loader.load_tools() == {}


def test_tool_loader_no_tools_flag():
    loader = ToolLoader(no_tools=True)
    assert loader.load_tools() == {}
