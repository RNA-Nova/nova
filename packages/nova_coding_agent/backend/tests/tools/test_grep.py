"""grep 工具测试：基础行为、pi 对齐（--hidden、长行截断、context 自渲染、整体
limit、No matches、literal）、事件循环不阻塞、子进程退出码透出、50KB 截断。
"""

import asyncio
import os
import tempfile

import pytest


def _load_executor():
    """从源码目录加载 grep 工具的 executor 模块。"""
    import importlib.util

    executor_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "tools", "grep.py"
    )
    spec = importlib.util.spec_from_file_location("_test_tool_grep", executor_path)
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


def test_grep(tmpdir):
    path = os.path.join(tmpdir, "sample.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write("def hello():\n    pass\n")

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": tmpdir, "pattern": "def hello"}))

    # 目录搜索时输出相对路径 + 行号格式（对齐 pi：path:N: text）
    assert "sample.py:1: def hello():" in result.content[0].text


def test_grep_limit_is_overall(tmpdir):
    """grep 的 limit 是整体匹配数上限（对齐 TS），不是 rg --max-count 的按文件语义。"""
    from nova_coding_agent.tools_common.operations import (
        GrepOptions,
        create_local_grep_operations,
    )

    # 3 个文件各 10 条匹配：per-file 语义给 15 条（5/文件），整体语义必须给 5 条。
    for i in range(3):
        with open(os.path.join(tmpdir, f"f{i}.py"), "w") as f:
            f.write("match\n" * 10)

    ops = create_local_grep_operations()

    async def scenario():
        return await ops.grep(tmpdir, GrepOptions(pattern="match", limit=5))

    result = _run(scenario())
    assert result.match_count == 5
    assert result.match_limit_reached is True


def test_tool_metadata_valid():
    """Tool 类必须声明完整元数据（类属性）。"""
    executor = _load_executor()
    assert executor.name == "grep"
    assert isinstance(executor.description, str) and executor.description
    assert isinstance(executor.parameters, dict)
    assert executor.parameters.get("type") == "object"


# ---------------------------------------------------------------------------
# pi 对齐行为：--hidden、长行截断、context 自渲染、No matches、literal、大小写
# ---------------------------------------------------------------------------


def test_grep_includes_hidden_files(tmpdir):
    """--hidden：隐藏文件与隐藏目录都参与搜索（对齐 pi）。"""
    from nova_coding_agent.tools_common.operations import (
        GrepOptions,
        create_local_grep_operations,
    )

    _write(os.path.join(tmpdir, ".hidden.py"), "match_me\n")
    _write(os.path.join(tmpdir, ".config", "inner.py"), "match_me\n")
    _write(os.path.join(tmpdir, "normal.py"), "match_me\n")

    result = _run(
        create_local_grep_operations().grep(tmpdir, GrepOptions(pattern="match_me"))
    )
    assert result.match_count == 3
    assert ".hidden.py" in result.content
    assert (
        os.path.join(".config", "inner.py") in result.content
        or ".config/inner.py" in result.content
    )


def test_grep_long_line_truncated(tmpdir):
    """超过 500 字符的行被截断并标注（防 minified 文件打爆上下文）。"""
    from nova_coding_agent.tools_common.operations import (
        GrepOptions,
        create_local_grep_operations,
    )

    _write(os.path.join(tmpdir, "min.js"), "x" * 800 + "match" + "y" * 800 + "\n")
    result = _run(
        create_local_grep_operations().grep(tmpdir, GrepOptions(pattern="match"))
    )
    assert result.match_count == 1
    assert result.lines_truncated is True
    assert "... [truncated]" in result.content
    assert "Some lines truncated" in result.content


def test_grep_context_rendering(tmpdir):
    """context=1：匹配行带 ':'，上下文行带 '-'（自渲染，不经 rg --context）。"""
    from nova_coding_agent.tools_common.operations import (
        GrepOptions,
        create_local_grep_operations,
    )

    _write(os.path.join(tmpdir, "a.py"), "line1\nline2\nline3\n")
    result = _run(
        create_local_grep_operations().grep(
            tmpdir, GrepOptions(pattern="line2", context=1)
        )
    )
    assert "a.py:2: line2" in result.content
    assert "a.py-1- line1" in result.content
    assert "a.py-3- line3" in result.content


def test_grep_no_matches(tmpdir):
    from nova_coding_agent.tools_common.operations import (
        GrepOptions,
        create_local_grep_operations,
    )

    _write(os.path.join(tmpdir, "a.py"), "nothing here\n")
    result = _run(
        create_local_grep_operations().grep(tmpdir, GrepOptions(pattern="zzz"))
    )
    assert result.no_matches is True
    assert result.match_count == 0


def test_grep_literal_mode(tmpdir):
    from nova_coding_agent.tools_common.operations import (
        GrepOptions,
        create_local_grep_operations,
    )

    _write(os.path.join(tmpdir, "a.py"), "a.b.c\naXbXc\n")
    result = _run(
        create_local_grep_operations().grep(
            tmpdir, GrepOptions(pattern="a.b.c", literal=True)
        )
    )
    assert result.match_count == 1
    assert "a.b.c" in result.content


def test_grep_case_default_sensitive(tmpdir):
    """默认区分大小写（对齐 pi）；ignoreCase=True 才不区分。"""
    from nova_coding_agent.tools_common.operations import (
        GrepOptions,
        create_local_grep_operations,
    )

    _write(os.path.join(tmpdir, "a.py"), "Match\nmatch\n")
    result = _run(
        create_local_grep_operations().grep(tmpdir, GrepOptions(pattern="match"))
    )
    assert result.match_count == 1
    result_ic = _run(
        create_local_grep_operations().grep(
            tmpdir, GrepOptions(pattern="match", ignore_case=True)
        )
    )
    assert result_ic.match_count == 2


# ---------------------------------------------------------------------------
# 事件循环不阻塞（parallel 工具与流式更新不被冻结）
# ---------------------------------------------------------------------------


def _populate_files(tmpdir, count=300, lines=500):
    for i in range(count):
        with open(os.path.join(tmpdir, f"file{i}.py"), "w") as f:
            f.write("x = 1\n" * lines)


def test_grep_does_not_block_event_loop(tmpdir):
    """grep 执行期间事件循环必须保持响应（parallel 工具与流式更新不被冻结）。

    旧的同步 subprocess.run 实现会让 ticker 一次都跑不到（ticks == 0）。
    """
    from nova_coding_agent.tools_common.operations import (
        GrepOptions,
        create_local_grep_operations,
    )

    _populate_files(tmpdir)

    async def scenario():
        ops = create_local_grep_operations()
        ticks = 0
        stop = False

        async def ticker():
            nonlocal ticks
            while not stop:
                ticks += 1
                await asyncio.sleep(0.002)

        ticker_task = asyncio.create_task(ticker())
        result = await ops.grep(tmpdir, GrepOptions(pattern="x = 1", limit=10))
        stop = True
        await ticker_task
        return ticks, result

    ticks, result = _run(scenario())
    assert ticks > 0
    assert result.match_count > 0


# ---------------------------------------------------------------------------
# 子进程退出码检查（rg 错误透出 stderr，不再静默误报无结果）
# ---------------------------------------------------------------------------


def test_grep_bad_regex_returns_error_result(tmpdir):
    """坏正则：rg 退出码 2，stderr 作为 is_error=True 的错误结果返回。"""
    from nova_harness.core.utils.binaries import resolve_binary

    if not resolve_binary("rg"):
        pytest.skip("rg 不可用")
    with open(os.path.join(tmpdir, "a.py"), "w", encoding="utf-8") as f:
        f.write("hello\n")

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": tmpdir, "pattern": "("}))

    assert result.is_error is True
    assert "regex parse error" in result.content[0].text


def test_grep_nonexistent_path_returns_path_not_found(tmpdir):
    """搜索路径不存在：is_error=True 且报 Path not found。"""
    executor = _load_executor()
    missing = os.path.join(tmpdir, "nope")
    result = _run(executor.execute("id", {"path": missing, "pattern": "x"}))

    assert result.is_error is True
    assert "Path not found" in result.content[0].text


def test_grep_nonexistent_path_raises_path_not_found(tmpdir):
    """搜索路径不存在：前置检查报 Path not found（rg/Python 兜底同语义）。"""
    from nova_coding_agent.tools_common.operations import (
        GrepOptions,
        create_local_grep_operations,
    )

    missing = os.path.join(tmpdir, "nope")
    with pytest.raises(RuntimeError, match="Path not found"):
        _run(create_local_grep_operations().grep(missing, GrepOptions(pattern="x")))


def test_grep_rg_bad_regex_surfaces_stderr(tmpdir):
    """rg 退出码 2（坏正则）：stderr 透出为 RuntimeError。"""
    from nova_harness.core.utils.binaries import resolve_binary

    from nova_coding_agent.tools_common.operations import (
        GrepOptions,
        create_local_grep_operations,
    )

    if not resolve_binary("rg"):
        pytest.skip("rg 不可用")
    _write(os.path.join(tmpdir, "a.py"), "hello\n")
    with pytest.raises(RuntimeError, match="regex parse error"):
        _run(create_local_grep_operations().grep(tmpdir, GrepOptions(pattern="(")))


def test_grep_rg_bad_glob_surfaces_stderr(tmpdir):
    """rg 退出码 2（坏 glob）：stderr 透出为 RuntimeError。"""
    from nova_harness.core.utils.binaries import resolve_binary

    from nova_coding_agent.tools_common.operations import (
        GrepOptions,
        create_local_grep_operations,
    )

    if not resolve_binary("rg"):
        pytest.skip("rg 不可用")
    _write(os.path.join(tmpdir, "a.py"), "hello\n")
    with pytest.raises(RuntimeError, match="error parsing glob"):
        _run(
            create_local_grep_operations().grep(
                tmpdir, GrepOptions(pattern="x", glob="[")
            )
        )


# ---------------------------------------------------------------------------
# 输出截断只按字节（对齐 pi 的 maxLines=∞，不再叠 2000 行上限）
# ---------------------------------------------------------------------------


def test_grep_output_not_line_capped(tmpdir):
    """grep 输出超 2000 行但未满 50KB 时不截断（对齐 pi maxLines=∞）。"""
    from nova_coding_agent.tools_common.operations import (
        GrepOptions,
        create_local_grep_operations,
    )

    content = "".join(f"m{i}\n" for i in range(2500))
    _write(os.path.join(tmpdir, "b.txt"), content)
    result = _run(
        create_local_grep_operations().grep(
            tmpdir, GrepOptions(pattern="m", limit=3000)
        )
    )
    assert result.match_count == 2500
    assert result.truncated is False
    assert "50KB limit reached" not in result.content
    assert "b.txt:2500: m2499" in result.content
