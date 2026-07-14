"""session_commands 扩展的单元测试。"""

import asyncio
import importlib.util
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _load_extension():
    """动态加载 session_commands extension 模块。"""
    ext_dir = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "extensions", "session_commands"
    )
    ext_path = os.path.join(ext_dir, "extension.py")
    spec = importlib.util.spec_from_file_location(
        "_test_session_commands_extension", ext_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(coro):
    return asyncio.run(coro)


class _FakeNovaAPI:
    """模拟 NovaExtensionAPI，仅记录 registerCommand 调用。"""

    def __init__(self):
        self.commands = {}

    def registerCommand(self, name: str, options: dict | None = None) -> None:
        self.commands[name] = options or {}


def test_extension_registers_all_commands():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    expected = {
        "compact",
        "fork",
        "clone",
        "export",
        "import",
        "model",
        "session",
        "name",
        "new",
        "reload",
        "tree",
        "trust",
        "untrust",
    }
    assert set(api.commands.keys()) == expected


def test_compact_calls_context_compact():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(compact=AsyncMock())
    _run(api.commands["compact"]["handler"]("", ctx))
    ctx.compact.assert_awaited_once_with()


def test_compact_passes_instructions():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(compact=AsyncMock())
    _run(api.commands["compact"]["handler"]("summarize recent changes", ctx))
    ctx.compact.assert_awaited_once_with(
        {"custom_instructions": "summarize recent changes"}
    )


def test_fork_parses_args():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(
        wait_for_idle=AsyncMock(),
        fork=AsyncMock(),
        send_message=AsyncMock(),
    )
    _run(api.commands["fork"]["handler"]("abc123 before", ctx))
    ctx.wait_for_idle.assert_awaited_once()
    ctx.fork.assert_awaited_once_with("abc123", position="before")


def test_fork_errors_without_entry_id():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(
        wait_for_idle=AsyncMock(),
        fork=AsyncMock(),
        send_message=AsyncMock(),
    )
    _run(api.commands["fork"]["handler"]("", ctx))
    ctx.fork.assert_not_awaited()
    ctx.send_message.assert_awaited_once()


def test_model_sets_model():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(set_model=AsyncMock())
    _run(api.commands["model"]["handler"]("openai/gpt-4o", ctx))
    ctx.set_model.assert_awaited_once_with("openai/gpt-4o")


def test_model_shows_current_when_no_args():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    class _Model:
        provider = "openai"
        id = "gpt-4o"

    ctx = SimpleNamespace(
        get_model=lambda: _Model(),
        send_message=AsyncMock(),
    )
    _run(api.commands["model"]["handler"]("", ctx))
    ctx.send_message.assert_awaited_once()
    assert "openai/gpt-4o" in ctx.send_message.await_args[0][0]["text"]


def test_session_sends_info():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(
        get_session_info=lambda: {
            "id": "sess-1",
            "name": "test",
            "cwd": "/tmp",
            "file": "/tmp/session.jsonl",
            "entry_count": 5,
            "leaf_id": "leaf-1",
            "persisted": True,
        },
        send_message=AsyncMock(),
    )
    _run(api.commands["session"]["handler"]("", ctx))
    ctx.send_message.assert_awaited_once()
    text = ctx.send_message.await_args[0][0]["text"]
    assert "sess-1" in text
    assert "test" in text
