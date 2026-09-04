"""ls 工具测试：基础列举、无法 stat 条目跳过、limit 早停、50KB 截断、prompt 元数据。"""

import asyncio
import os
import tempfile

import pytest


def _load_executor():
    """从源码目录加载 ls 工具的 executor 模块。"""
    import importlib.util

    executor_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "tools", "ls.py"
    )
    spec = importlib.util.spec_from_file_location("_test_tool_ls", executor_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from nova_harness.core.types.resources.tools import (
        NULL_TOOL_SETTINGS,
        ToolContext,
    )

    context = ToolContext(cwd=os.getcwd(), settings=NULL_TOOL_SETTINGS)
    return module.Tool(context)


def _run(coro):
    return asyncio.run(coro)


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


@pytest.fixture
def tmpdir():
    # realpath：fd/rg 会规范化搜索根（macOS /var 软链），相对化前缀才干净
    with tempfile.TemporaryDirectory() as d:
        yield os.path.realpath(d)


def test_ls_lists_directory(tmpdir):
    os.makedirs(os.path.join(tmpdir, "sub"))
    open(os.path.join(tmpdir, "a.txt"), "w").close()

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": tmpdir}))

    text = result.content[0].text
    assert "a.txt" in text
    assert "sub/" in text
    assert result.details["displayed"] == 2


def test_tool_metadata_valid():
    """Tool 类必须声明完整元数据（类属性）。"""
    executor = _load_executor()
    assert executor.name == "ls"
    assert isinstance(executor.description, str) and executor.description
    assert isinstance(executor.parameters, dict)
    assert executor.parameters.get("type") == "object"


def test_ls_prompt_snippet_aligned_with_pi():
    """ls 补齐  promptSnippet（纯增量元数据，不动对外契约）。"""
    executor = _load_executor()
    assert executor.prompt_snippet == "List directory contents"


# ---------------------------------------------------------------------------
# 无法 stat 的条目跳过、达 limit 即停
# ---------------------------------------------------------------------------


def test_ls_skips_unstatable_entries(tmpdir):
    """悬空软链等无法 stat 的条目直接跳过。"""
    from nova_coding_agent.tools_common.operations import LocalLsOperations, LsOptions

    _write(os.path.join(tmpdir, "real.txt"), "x")
    os.symlink(os.path.join(tmpdir, "missing-target"), os.path.join(tmpdir, "dangling"))
    entries, truncated = _run(LocalLsOperations().list_dir(LsOptions(path=str(tmpdir))))
    assert [e.name for e in entries] == ["real.txt"]
    assert truncated is False


def test_ls_limit_stops_early_and_marks_truncated(tmpdir):
    """达 limit 即停并标记 truncated；恰好等于 limit 时不标记（）。"""
    from nova_coding_agent.tools_common.operations import LocalLsOperations, LsOptions

    for i in range(5):
        _write(os.path.join(tmpdir, f"e{i}.txt"), "x")
    entries, truncated = _run(
        LocalLsOperations().list_dir(LsOptions(path=str(tmpdir), limit=2))
    )
    assert [e.name for e in entries] == ["e0.txt", "e1.txt"]
    assert truncated is True
    entries, truncated = _run(
        LocalLsOperations().list_dir(LsOptions(path=str(tmpdir), limit=5))
    )
    assert len(entries) == 5
    assert truncated is False


# ---------------------------------------------------------------------------
# 输出截断只按字节（不再叠 2000 行上限）
# ---------------------------------------------------------------------------


def test_ls_output_truncated_at_50kb(tmpdir):
    """ls 输出拼接后过 truncate_head：超 50KB 截断并标注（对齐 grep）。"""
    for i in range(500):
        name = f"file-{i:04d}-" + "x" * 100 + ".txt"
        open(os.path.join(tmpdir, name), "w").close()

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": tmpdir}))

    text = result.content[0].text
    assert "50KB limit reached" in text
    assert text.count("file-") < 500  # 条目确被截断，非全量输出


def test_ls_output_not_line_capped(tmpdir):
    """ls 输出超 2000 行但未满 50KB 时不截断。"""
    for i in range(2100):
        open(os.path.join(tmpdir, f"f{i:04d}.txt"), "w").close()

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": tmpdir, "limit": 3000}))

    text = result.content[0].text
    assert "50KB limit reached" not in text
    assert "f2099.txt" in text
    assert result.details["displayed"] == 2100
