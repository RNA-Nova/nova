"""todo 工具测试（全量替换语义）。

工具零服务端状态：execute 只校验 + 把清单写进 details；状态的单一事实源
是会话历史里最新一条工具结果的 details（分支安全由会话树天然保证）。
"""

import asyncio
import importlib.util
import os


def _load_todo_tool():
    """加载 tools/todo.py 并构造 Tool 实例。"""
    tool_path = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "todo.py")
    spec = importlib.util.spec_from_file_location("_test_tool_todo", tool_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from nova_harness.types.resources.tools import (
        NULL_TOOL_SETTINGS,
        ToolContext,
    )

    context = ToolContext(cwd=os.getcwd(), settings=NULL_TOOL_SETTINGS)
    return module.Tool(context)


def test_todo_full_replace_success():
    """合法清单：details 携带归一化后的全量列表，文本含进度统计。"""
    tool = _load_todo_tool()
    result = asyncio.run(
        tool.execute(
            "id",
            {
                "todos": [
                    {"content": "写测试", "status": "completed"},
                    {"content": "修 bug", "status": "in_progress"},
                    {"content": "  发版  ", "status": "pending"},
                ]
            },
        )
    )
    assert result.is_error is False
    todos = result.details["todos"]
    assert [t["content"] for t in todos] == ["写测试", "修 bug", "发版"]  # 空白被规整
    assert todos[1]["status"] == "in_progress"
    assert "1/3 completed" in result.content[0].text
    assert "1 in progress" in result.content[0].text


def test_todo_empty_list_clears():
    """空数组 = 清空（合法快照）。"""
    tool = _load_todo_tool()
    result = asyncio.run(tool.execute("id", {"todos": []}))
    assert result.is_error is False
    assert result.details["todos"] == []
    assert "cleared" in result.content[0].text


def test_todo_missing_param_is_error():
    """缺 todos 参数：is_error + details.error。"""
    tool = _load_todo_tool()
    result = asyncio.run(tool.execute("id", {}))
    assert result.is_error is True
    assert "todos" in result.details["error"]


def test_todo_invalid_status_is_error():
    """非法 status：指明位置与合法枚举。"""
    tool = _load_todo_tool()
    result = asyncio.run(
        tool.execute("id", {"todos": [{"content": "x", "status": "done"}]})
    )
    assert result.is_error is True
    assert "todos[0].status" in result.details["error"]
    assert "pending" in result.details["error"]


def test_todo_empty_content_is_error():
    """空白 content 拒绝。"""
    tool = _load_todo_tool()
    result = asyncio.run(
        tool.execute("id", {"todos": [{"content": "   ", "status": "pending"}]})
    )
    assert result.is_error is True
    assert "content" in result.details["error"]


def test_todo_no_server_side_state():
    """两次调用互不影响——状态完全由调用方（模型发全量清单）驱动。"""
    tool = _load_todo_tool()
    first = asyncio.run(
        tool.execute("id1", {"todos": [{"content": "a", "status": "pending"}]})
    )
    second = asyncio.run(
        tool.execute("id2", {"todos": [{"content": "b", "status": "completed"}]})
    )
    assert [t["content"] for t in first.details["todos"]] == ["a"]
    assert [t["content"] for t in second.details["todos"]] == ["b"]
