"""edit 工具测试：批量编辑、CRLF/LF 字节保真、access fail-fast、prompt 元数据。"""

import asyncio
import os
import tempfile

import pytest


def _load_executor():
    """从源码目录加载 edit 工具的 executor 模块。"""
    import importlib.util

    executor_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "tools", "edit.py"
    )
    spec = importlib.util.spec_from_file_location("_test_tool_edit", executor_path)
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


@pytest.fixture
def tmpdir():
    # realpath：fd/rg 会规范化搜索根（macOS /var 软链），相对化前缀才干净
    with tempfile.TemporaryDirectory() as d:
        yield os.path.realpath(d)


def test_edit_batch(tmpdir):
    path = os.path.join(tmpdir, "edit.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("foo bar baz")

    executor = _load_executor()
    result = _run(
        executor.execute(
            "id",
            {
                "path": path,
                "edits": [
                    {"oldText": "foo", "newText": "FOO"},
                    {"oldText": "bar", "newText": "BAR"},
                ],
            },
        )
    )

    with open(path, "r", encoding="utf-8") as f:
        assert f.read() == "FOO BAR baz"
    assert "Successfully replaced 2 block(s)" in result.content[0].text
    assert result.details["first_changed_line"] == 1
    assert "-1 foo bar baz" in result.details["diff"]
    assert "+1 FOO BAR baz" in result.details["diff"]


def test_tool_metadata_valid():
    """Tool 类必须声明完整元数据（类属性）。"""
    executor = _load_executor()
    assert executor.name == "edit"
    assert isinstance(executor.description, str) and executor.description
    assert isinstance(executor.parameters, dict)
    assert executor.parameters.get("type") == "object"


# ---------------------------------------------------------------------------
# CRLF 换行符保真（edit 走二进制 I/O，不经 universal newlines 转换）
# ---------------------------------------------------------------------------


def test_edit_preserves_crlf_line_endings(tmpdir):
    """CRLF 文件编辑后写回仍是 CRLF（detect/restore_line_endings 不再形同虚设）。"""
    path = os.path.join(tmpdir, "win.txt")
    with open(path, "wb") as f:
        f.write(b"line1\r\nline2\r\nline3\r\n")

    executor = _load_executor()
    result = _run(
        executor.execute(
            "id", {"path": path, "edits": [{"oldText": "line2", "newText": "LINE2"}]}
        )
    )

    assert not result.is_error
    with open(path, "rb") as f:
        assert f.read() == b"line1\r\nLINE2\r\nline3\r\n"


def test_edit_preserves_lf_line_endings(tmpdir):
    """LF 文件编辑后不引入 \\r（写回不依赖平台文本模式转换）。"""
    path = os.path.join(tmpdir, "unix.txt")
    with open(path, "wb") as f:
        f.write(b"alpha\nbeta\n")

    executor = _load_executor()
    result = _run(
        executor.execute(
            "id", {"path": path, "edits": [{"oldText": "beta", "newText": "BETA"}]}
        )
    )

    assert not result.is_error
    with open(path, "rb") as f:
        assert f.read() == b"alpha\nBETA\n"


def test_edit_operations_read_write_byte_faithful(tmpdir):
    """LocalEditOperations 读写均字节保真：读不回译 \\r\\n，写不转换 \\n。"""
    from nova_coding_agent.tools_common.operations import LocalEditOperations

    path = os.path.join(tmpdir, "mixed.txt")
    original = b"a\r\nb\nc\r\n"
    with open(path, "wb") as f:
        f.write(original)

    ops = LocalEditOperations()
    text = _run(ops.read_text(path))
    assert text == original.decode("utf-8")  # \r\n 未被 universal newlines 吃掉
    _run(ops.write_text(path, text))
    with open(path, "rb") as f:
        assert f.read() == original


# ---------------------------------------------------------------------------
# edit 的 access fail-fast 与 prompt 元数据（对齐 pi edit.ts）
# ---------------------------------------------------------------------------


def test_edit_nonexistent_file_access_error(tmpdir):
    """文件不存在：access 检查 fail-fast 报 ENOENT（对齐 pi access(R_OK|W_OK)）。"""
    executor = _load_executor()
    missing = os.path.join(tmpdir, "nope.txt")
    result = _run(
        executor.execute(
            "id", {"path": missing, "edits": [{"oldText": "a", "newText": "b"}]}
        )
    )

    assert result.is_error is True
    assert "ENOENT" in result.content[0].text


def test_edit_readonly_file_fails_fast(tmpdir):
    """只读文件：写盘之前就报错（读写权限检查），文件内容保持不变。"""
    path = os.path.join(tmpdir, "ro.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("hello")
    os.chmod(path, 0o444)
    try:
        if os.access(path, os.W_OK):
            pytest.skip("当前用户不受权限位约束（如 root），拦截不生效")
        executor = _load_executor()
        result = _run(
            executor.execute(
                "id",
                {"path": path, "edits": [{"oldText": "hello", "newText": "HI"}]},
            )
        )

        assert result.is_error is True
        assert "EACCES" in result.content[0].text
        with open(path, "r", encoding="utf-8") as f:
            assert f.read() == "hello"
    finally:
        os.chmod(path, 0o644)


def test_edit_prompt_metadata():
    """edit 声明 prompt_snippet 与 prompt_guidelines（对齐 pi promptSnippet/Guidelines）。"""
    executor = _load_executor()
    assert isinstance(executor.prompt_snippet, str) and executor.prompt_snippet
    guidelines = executor.prompt_guidelines
    assert isinstance(guidelines, list) and guidelines
    assert all(isinstance(g, str) and g for g in guidelines)
