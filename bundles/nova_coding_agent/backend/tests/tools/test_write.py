"""write tool executor 单元测试（对齐 pi write.ts 行为）。"""

import asyncio
import os
import tempfile

import pytest


def _load_executor(settings=None):
    import importlib.util

    executor_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "tools", "write.py"
    )
    spec = importlib.util.spec_from_file_location("_test_tool_write", executor_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from nova_harness.core.types.resources.tools import (
        NULL_TOOL_SETTINGS,
        ToolContext,
    )

    context = ToolContext(cwd=os.getcwd(), settings=settings or NULL_TOOL_SETTINGS)
    return module.Tool(context)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_write_overwrite_existing(tmpdir):
    """覆盖已有文件：action 为"覆盖"，内容整体替换（对齐 pi overwrite 语义）。"""
    path = os.path.join(tmpdir, "file.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("old content")

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": path, "content": "new content"}))

    assert result.is_error is False
    assert result.details["action"] == "覆盖"
    assert result.details["chars"] == len("new content")
    with open(path, "r", encoding="utf-8") as f:
        assert f.read() == "new content"


def test_write_creates_parent_dirs(tmpdir):
    """自动创建缺失的父目录（对齐 pi ops.mkdir(dirname)）。"""
    path = os.path.join(tmpdir, "a", "b", "c", "file.txt")
    executor = _load_executor()
    result = _run(executor.execute("id", {"path": path, "content": "nested"}))

    assert result.is_error is False
    assert result.details["action"] == "创建"
    with open(path, "r", encoding="utf-8") as f:
        assert f.read() == "nested"


def test_write_missing_path():
    """缺 path 参数：is_error 返回（schema 之外的防御）。"""
    executor = _load_executor()
    result = _run(executor.execute("id", {"content": "x"}))

    assert result.is_error is True
    assert "必须提供 path 参数" in result.content[0].text
    assert result.details["error"] == "Missing required parameter: path"


def test_write_missing_content(tmpdir):
    """缺 content 参数：is_error 返回。"""
    executor = _load_executor()
    result = _run(executor.execute("id", {"path": os.path.join(tmpdir, "file.txt")}))

    assert result.is_error is True
    assert "必须提供 content 参数" in result.content[0].text
    assert result.details["error"] == "Missing required parameter: content"


def test_write_content_not_string(tmpdir):
    """content 非字符串：is_error 返回，不落盘。"""
    path = os.path.join(tmpdir, "file.txt")
    executor = _load_executor()
    result = _run(executor.execute("id", {"path": path, "content": 123}))

    assert result.is_error is True
    assert not os.path.exists(path)


def test_write_pre_aborted_signal(tmpdir):
    """spawn 前 signal 已中止：is_error 返回且不产生任何文件副作用（对齐 pi）。"""
    from nova_ai import AbortController

    executor = _load_executor()
    controller = AbortController()
    controller.abort()
    path = os.path.join(tmpdir, "should_not_exist.txt")
    result = _run(
        executor.execute(
            "id",
            {"path": path, "content": "x"},
            signal=controller.signal,
        )
    )

    assert result.is_error is True
    assert result.details["error"] == "Operation aborted"
    assert not os.path.exists(path)


def test_write_encoding_param(tmpdir):
    """encoding 参数（nova 增量）：按指定编码字节级落盘。"""
    path = os.path.join(tmpdir, "latin1.txt")
    executor = _load_executor()
    result = _run(
        executor.execute(
            "id",
            {"path": path, "content": "café", "encoding": "latin-1"},
        )
    )

    assert result.is_error is False
    with open(path, "rb") as f:
        assert f.read() == "café".encode("latin-1")


def test_write_concurrent_same_file_serialized(tmpdir):
    """同一路径并发写经写锁完整串行（对齐 pi withFileMutationQueue）：

    最终文件必然是某一次写入的完整内容，不出现交错。
    """
    path = os.path.join(tmpdir, "race.txt")
    executor = _load_executor()
    content_a = "A" * 10000
    content_b = "B" * 10000

    async def run_concurrent():
        return await asyncio.gather(
            executor.execute("id-a", {"path": path, "content": content_a}),
            executor.execute("id-b", {"path": path, "content": content_b}),
        )

    results = _run(run_concurrent())
    assert all(not r.is_error for r in results)
    with open(path, "r", encoding="utf-8") as f:
        final = f.read()
    assert final in (content_a, content_b)


# ---------------------------------------------------------------------------
# 字节保真与元数据
# ---------------------------------------------------------------------------


def test_write_content_byte_exact(tmpdir):
    """write 工具内容字节级保真（含 \\r\\n，不经平台换行转换）。"""
    path = os.path.join(tmpdir, "out.txt")
    content = "x\r\ny\nz\r\n"

    executor = _load_executor()
    result = _run(executor.execute("id", {"path": path, "content": content}))

    assert not result.is_error
    with open(path, "rb") as f:
        assert f.read() == content.encode("utf-8")


def test_tool_metadata_valid():
    """Tool 类必须声明完整元数据（类属性）。"""
    executor = _load_executor()
    assert executor.name == "write"
    assert isinstance(executor.description, str) and executor.description
    assert isinstance(executor.parameters, dict)
    assert executor.parameters.get("type") == "object"
