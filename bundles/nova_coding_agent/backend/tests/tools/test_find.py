"""find 工具测试：基础行为、fd 层语义、rg 中间层（fd → rg → python 三级链）、
子进程退出码透出、50KB 截断、prompt 元数据。
"""

import asyncio
import os
import tempfile

import pytest


def _load_executor():
    """从源码目录加载 find 工具的 executor 模块。"""
    import importlib.util

    executor_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "tools", "find.py"
    )
    spec = importlib.util.spec_from_file_location("_test_tool_find", executor_path)
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


def test_find_file(tmpdir):
    open(os.path.join(tmpdir, "target.py"), "w").close()
    open(os.path.join(tmpdir, "other.txt"), "w").close()

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": tmpdir, "pattern": "*.py"}))

    assert "target.py" in result.content[0].text
    assert "other.txt" not in result.content[0].text


def test_find_limit_declared_in_schema():
    """find 的 limit 参数必须在 schema properties 中声明（截断提示对应 limit 用法）。"""
    executor = _load_executor()
    props = executor.parameters["properties"]
    assert "limit" in props
    assert props["limit"]["type"] == "integer"
    assert props["limit"]["default"] == 1000


def test_tool_metadata_valid():
    """Tool 类必须声明完整元数据（类属性）。"""
    executor = _load_executor()
    assert executor.name == "find"
    assert isinstance(executor.description, str) and executor.description
    assert isinstance(executor.parameters, dict)
    assert executor.parameters.get("type") == "object"


def test_find_prompt_snippet_aligned_with_pi():
    """find 补齐  promptSnippet（纯增量元数据，不动对外契约）。"""
    executor = _load_executor()
    assert executor.prompt_snippet == "Find files by glob pattern (respects .gitignore)"


# ---------------------------------------------------------------------------
# fd 层语义（）：相对化输出、--hidden、空 pattern、--full-path
# ---------------------------------------------------------------------------


def test_find_results_relativized(tmpdir):
    """结果相对搜索根（posix），不返回绝对路径。"""
    from nova_coding_agent.tools_common.operations import (
        FindOptions,
        create_local_find_operations,
    )

    _write(os.path.join(tmpdir, "sub", "a.py"), "x")
    _write(os.path.join(tmpdir, "b.py"), "x")
    results = _run(
        create_local_find_operations().find(FindOptions(path=tmpdir, pattern="*.py"))
    )
    assert sorted(results) == ["b.py", "sub/a.py"]


def test_find_includes_hidden(tmpdir):
    """--hidden：隐藏文件参与查找（）。"""
    from nova_coding_agent.tools_common.operations import (
        FindOptions,
        create_local_find_operations,
    )

    _write(os.path.join(tmpdir, ".hidden.py"), "x")
    _write(os.path.join(tmpdir, "normal.py"), "x")
    results = _run(
        create_local_find_operations().find(FindOptions(path=tmpdir, pattern="*.py"))
    )
    assert ".hidden.py" in results
    assert "normal.py" in results


def test_find_empty_pattern_matches_all(tmpdir):
    """pattern 缺省时匹配全部文件（fd 的 pattern 槽传 '*'）。"""
    from nova_coding_agent.tools_common.operations import (
        FindOptions,
        create_local_find_operations,
    )

    _write(os.path.join(tmpdir, "a.py"), "x")
    _write(os.path.join(tmpdir, "b.txt"), "x")
    results = _run(create_local_find_operations().find(FindOptions(path=tmpdir)))
    assert "a.py" in results
    assert "b.txt" in results


def test_find_full_path_pattern(tmpdir):
    """带 / 的 pattern（如 sub/*.py）经 --full-path 正确匹配（fd 可用时）。"""
    from nova_coding_agent.tools_common.operations import (
        FindOptions,
        create_local_find_operations,
    )

    from nova_harness.core.utils.binaries import resolve_binary

    if not resolve_binary("fd"):
        pytest.skip("fd 不可用")
    _write(os.path.join(tmpdir, "sub", "a.py"), "x")
    _write(os.path.join(tmpdir, "b.py"), "x")
    results = _run(
        create_local_find_operations().find(
            FindOptions(path=tmpdir, pattern="sub/*.py")
        )
    )
    assert results == ["sub/a.py"]


def test_find_directory_type(tmpdir):
    """type=directory（Nova 超集）：只列目录。"""
    from nova_coding_agent.tools_common.operations import (
        FindOptions,
        create_local_find_operations,
    )

    os.makedirs(os.path.join(tmpdir, "sub"))
    _write(os.path.join(tmpdir, "a.py"), "x")
    results = _run(
        create_local_find_operations().find(
            FindOptions(path=tmpdir, find_type="directory")
        )
    )
    assert results == ["sub"]


# ---------------------------------------------------------------------------
# 事件循环不阻塞（parallel 工具与流式更新不被冻结）
# ---------------------------------------------------------------------------


def _populate_files(tmpdir, count=300, lines=500):
    for i in range(count):
        with open(os.path.join(tmpdir, f"file{i}.py"), "w") as f:
            f.write("x = 1\n" * lines)


def test_find_does_not_block_event_loop(tmpdir):
    """find 执行期间事件循环必须保持响应。"""
    from nova_coding_agent.tools_common.operations import (
        FindOptions,
        create_local_find_operations,
    )

    _populate_files(tmpdir)

    async def scenario():
        ops = create_local_find_operations()
        ticks = 0
        stop = False

        async def ticker():
            nonlocal ticks
            while not stop:
                ticks += 1
                await asyncio.sleep(0.002)

        ticker_task = asyncio.create_task(ticker())
        results = await ops.find(FindOptions(path=tmpdir, pattern="*.py"))
        stop = True
        await ticker_task
        return ticks, results

    ticks, results = _run(scenario())
    assert ticks > 0
    assert len(results) == 300


# ---------------------------------------------------------------------------
# find 的 rg 中间层（fd → rg → python）
# ---------------------------------------------------------------------------


def test_find_with_rg_tier(tmpdir):
    """rg --files 层：相对化输出、glob、limit 截断。"""
    from nova_coding_agent.tools_common.operations import (
        FindOptions,
        create_local_find_operations,
    )

    from nova_harness.core.utils.binaries import resolve_binary

    rg_path = resolve_binary("rg")
    if not rg_path:
        pytest.skip("rg 不可用")

    for name in ("a.py", "b.py", "c.txt"):
        with open(os.path.join(tmpdir, name), "w") as f:
            f.write("x")

    ops = create_local_find_operations()
    results = _run(
        ops._find_with_rg(rg_path, FindOptions(pattern="*.py", path=str(tmpdir)))
    )
    # 输出相对搜索根（posix），不再返回绝对路径
    assert sorted(results) == ["a.py", "b.py"]

    limited = _run(
        ops._find_with_rg(
            rg_path, FindOptions(pattern="*.py", path=str(tmpdir), limit=1)
        )
    )
    assert len(limited) == 1


def test_find_three_tier_fallback_order(tmpdir, monkeypatch):
    """无 fd 有 rg 时 find 走 rg 层；皆无时走便携引擎。"""
    import nova_coding_agent.tools_common.process_runner as runner_mod
    from nova_coding_agent.tools_common.operations import (
        FindOptions,
        LocalFindOperations,
    )

    with open(os.path.join(tmpdir, "x.py"), "w") as f:
        f.write("x")

    calls = []
    real_resolve = runner_mod.resolve_binary

    def fake_resolve(name):
        calls.append(name)
        if name == "fd":
            return None  # 无 fd
        return real_resolve(name)

    # 二进制解析已归 ProcessRunner（runner_mod.resolve_binary）
    monkeypatch.setattr(runner_mod, "resolve_binary", fake_resolve)
    ops = LocalFindOperations(runner=runner_mod.LocalProcessRunner())
    results = _run(ops.find(FindOptions(pattern="*.py", path=str(tmpdir))))
    assert results and results[0].endswith("x.py")
    assert calls[:2] == ["fd", "rg"]  # fd 未命中 → rg

    # 全部未命中 → 便携引擎兜底
    monkeypatch.setattr(runner_mod, "resolve_binary", lambda name: None)
    results = _run(ops.find(FindOptions(pattern="*.py", path=str(tmpdir))))
    assert results and results[0].endswith("x.py")


# ---------------------------------------------------------------------------
# 子进程退出码检查（fd/rg 错误透出 stderr，不再静默误报无结果）
# ---------------------------------------------------------------------------


def test_find_nonexistent_path_returns_path_not_found(tmpdir):
    """查找路径不存在：is_error=True 且报 Path not found。"""
    executor = _load_executor()
    missing = os.path.join(tmpdir, "nope")
    result = _run(executor.execute("id", {"path": missing}))

    assert result.is_error is True
    assert "Path not found" in result.content[0].text


def test_find_nonexistent_path_raises_path_not_found(tmpdir):
    """查找路径不存在：前置检查报 Path not found（fd/rg/Python 同语义）。"""
    from nova_coding_agent.tools_common.operations import (
        FindOptions,
        create_local_find_operations,
    )

    missing = os.path.join(tmpdir, "nope")
    with pytest.raises(RuntimeError, match="Path not found"):
        _run(create_local_find_operations().find(FindOptions(path=missing)))


def test_find_fd_bad_glob_surfaces_stderr(tmpdir):
    """fd 错误退出码为 1（与 rg 不同）：坏 glob 时 stderr 透出为 RuntimeError。"""
    from nova_coding_agent.tools_common.operations import (
        FindOptions,
        create_local_find_operations,
    )

    from nova_harness.core.utils.binaries import resolve_binary

    if not resolve_binary("fd"):
        pytest.skip("fd 不可用")
    with pytest.raises(RuntimeError, match="error parsing glob"):
        _run(create_local_find_operations().find(FindOptions(path=tmpdir, pattern="[")))


def test_find_rg_tier_bad_glob_surfaces_stderr(tmpdir):
    """rg --files 层退出码 2（坏 glob）：stderr 透出为 RuntimeError。"""
    from nova_coding_agent.tools_common.operations import (
        FindOptions,
        create_local_find_operations,
    )

    from nova_harness.core.utils.binaries import resolve_binary

    rg_path = resolve_binary("rg")
    if not rg_path:
        pytest.skip("rg 不可用")
    ops = create_local_find_operations()
    with pytest.raises(RuntimeError, match="error parsing glob"):
        _run(ops._find_with_rg(rg_path, FindOptions(path=tmpdir, pattern="[")))


# ---------------------------------------------------------------------------
# 输出截断只按字节（不再叠 2000 行上限）
# ---------------------------------------------------------------------------


def test_find_output_truncated_at_50kb(tmpdir):
    """find 输出拼接后过 truncate_head：超 50KB 截断并标注（对齐 grep）。"""
    for i in range(500):
        name = f"file-{i:04d}-" + "x" * 100 + ".txt"
        open(os.path.join(tmpdir, name), "w").close()

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": tmpdir, "pattern": "*.txt"}))

    text = result.content[0].text
    assert "50KB limit reached" in text
    assert text.count("file-") < 500  # 条目确被截断，非全量输出


def test_find_output_not_line_capped(tmpdir):
    """find 输出超 2000 行但未满 50KB 时不截断。"""
    for i in range(2100):
        open(os.path.join(tmpdir, f"f{i:04d}.txt"), "w").close()

    executor = _load_executor()
    # realpath：fd 会规范化搜索根（macOS /var 软链），相对化前缀才干净
    result = _run(
        executor.execute(
            "id",
            {"path": os.path.realpath(tmpdir), "pattern": "*.txt", "limit": 3000},
        )
    )

    text = result.content[0].text
    assert "50KB limit reached" not in text
    assert "f2099.txt" in text  # 第 2000 行之后的结果仍在输出中


# ---------------------------------------------------------------------------
# fd 部分产出容忍与行尾 \r 清理（假 ProcessSession 确定性触发——
# 不走平台二进制（shell 脚本桩在 Windows 不可执行），全平台可跑）
# ---------------------------------------------------------------------------


class _FakeFdSession:
    """假 fd 进程会话：按脚本吐行、报退出码与 stderr。

    行尾 ``\\r`` 清洗是 ProcessSession 的契约（真实现见
    tools_common/streams.read_lines，其 CRLF 行为由 test_streams 覆盖）——
    本桩复刻同一契约，fd 层断言聚焦"清洗后的行原样进结果"。
    """

    def __init__(self, lines, exit_code, stderr=""):
        self._lines = lines
        self._exit_code = exit_code
        self._stderr = stderr

    async def stdout_lines(self):
        for line in self._lines:
            yield line.removesuffix("\r")

    async def terminate(self):
        pass

    async def wait(self):
        return self._exit_code

    async def stderr_text(self):
        return self._stderr


class _FakeFdRunner:
    """spawn 即返回预定会话的 runner。"""

    def __init__(self, session):
        self._session = session

    async def spawn(self, argv, cwd):
        return self._session


def _find_with_fake_fd(tmpdir, lines, exit_code, stderr=""):
    from nova_coding_agent.tools_common.operations import (
        FindOptions,
        LocalFindOperations,
    )

    operations = LocalFindOperations(
        runner=_FakeFdRunner(_FakeFdSession(lines, exit_code, stderr))
    )
    return _run(operations._find_with_fd("fake-fd", FindOptions(path=str(tmpdir))))


def test_find_fd_partial_output_tolerated_on_nonzero_exit(tmpdir):
    """fd 非零退出但有产出时保留部分结果（仅无输出才报错）。"""
    results = _find_with_fake_fd(tmpdir, [os.path.join(str(tmpdir), "some.py")], 1)
    assert results == ["some.py"]


def test_find_fd_strips_trailing_carriage_return(tmpdir):
    """fd 输出行尾 \\r 被清理（Windows \\r\\n 行尾）。"""
    results = _find_with_fake_fd(
        tmpdir, [os.path.join(str(tmpdir), "crlf.py") + "\r"], 0
    )
    assert results == ["crlf.py"]


def test_find_fd_error_without_output_raises(tmpdir):
    """fd 非零退出且无产出：stderr 透出为 RuntimeError。"""
    with pytest.raises(RuntimeError, match="fake fd exploded"):
        _find_with_fake_fd(tmpdir, [], 1, stderr="fake fd exploded")
