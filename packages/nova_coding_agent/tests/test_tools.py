"""nova_coding_agent 内置工具基础测试。"""

import asyncio
import json
import os
import tempfile

import pytest


def _load_executor(tool_name: str):
    """从源码目录加载指定工具的 executor 模块。"""
    import importlib.util

    tool_dir = os.path.join(os.path.dirname(__file__), "..", "tools", tool_name)
    executor_path = os.path.join(tool_dir, "executor.py")
    spec = importlib.util.spec_from_file_location(
        f"_test_tool_{tool_name}", executor_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ToolExecutor()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_read_text_file(tmpdir):
    path = os.path.join(tmpdir, "sample.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("line1\nline2\nline3\n")

    executor = _load_executor("read")
    result = _run(executor.execute("id", {"path": path}))

    assert len(result.content) == 1
    assert "line1" in result.content[0].text
    assert result.details["lines"] == 3


def test_read_offset_limit(tmpdir):
    path = os.path.join(tmpdir, "sample.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("a\nb\nc\nd\n")

    executor = _load_executor("read")
    result = _run(executor.execute("id", {"path": path, "offset": 2, "limit": 2}))

    # 提取 ``` 代码块内容，避免路径中包含字母造成误判
    text = result.content[0].text
    parts = text.split("```")
    code = parts[1].split("\n", 1)[-1].strip("\n`")
    lines = [ln for ln in code.splitlines() if ln]
    assert lines == ["b", "c"]


def test_write_creates_file(tmpdir):
    path = os.path.join(tmpdir, "nested", "file.txt")
    executor = _load_executor("write")
    result = _run(executor.execute("id", {"path": path, "content": "hello"}))

    assert os.path.exists(path)
    with open(path, "r", encoding="utf-8") as f:
        assert f.read() == "hello"
    assert result.details["action"] == "创建"


def test_edit_batch(tmpdir):
    path = os.path.join(tmpdir, "edit.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("foo bar baz")

    executor = _load_executor("edit")
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
    assert result.details["replacements"] == 2


def test_bash_echo(tmpdir):
    executor = _load_executor("bash")
    result = _run(executor.execute("id", {"command": "echo hello", "cwd": tmpdir}))

    assert result.details["returncode"] == 0
    assert "hello" in result.content[0].text


def test_ls_lists_directory(tmpdir):
    os.makedirs(os.path.join(tmpdir, "sub"))
    open(os.path.join(tmpdir, "a.txt"), "w").close()

    executor = _load_executor("ls")
    result = _run(executor.execute("id", {"path": tmpdir}))

    text = result.content[0].text
    assert "a.txt" in text
    assert "sub/" in text
    assert result.details["total"] == 2


def test_find_file(tmpdir):
    open(os.path.join(tmpdir, "target.py"), "w").close()
    open(os.path.join(tmpdir, "other.txt"), "w").close()

    executor = _load_executor("find")
    result = _run(executor.execute("id", {"path": tmpdir, "glob": "*.py"}))

    assert "target.py" in result.content[0].text
    assert "other.txt" not in result.content[0].text


def test_grep(tmpdir):
    path = os.path.join(tmpdir, "sample.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write("def hello():\n    pass\n")

    executor = _load_executor("grep")
    result = _run(executor.execute("id", {"path": tmpdir, "regex": "def hello"}))

    assert "sample.py" in result.content[0].text
    assert "def hello" in result.content[0].text


def test_schema_json_valid():
    for tool_name in ["bash", "edit", "find", "grep", "ls", "read", "write"]:
        tool_dir = os.path.join(os.path.dirname(__file__), "..", "tools", tool_name)
        schema_path = os.path.join(tool_dir, "schema.json")
        with open(schema_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "name" in data
        assert "parameters" in data
