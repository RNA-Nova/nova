"""session_commands 扩展的单元测试。"""

import asyncio
import importlib.util
import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


def _load_extension():
    """动态加载 session_commands extension 模块。"""
    ext_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "extensions", "session_commands.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_test_session_commands_extension", ext_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(coro):
    return asyncio.run(coro)


class _FakeNovaAPI:
    """模拟 NovaExtensionAPI，记录 registerCommand / on 调用。"""

    def __init__(self):
        self.commands = {}
        self.handlers = {}

    def registerCommand(self, name: str, options: dict | None = None) -> None:
        self.commands[name] = options or {}

    def on(self, event_type, handler):
        self.handlers[event_type] = handler
        return lambda: None


class _FakeUI:
    """模拟泛型 UIContext：request 按脚本返回（默认返回第一个选项）。"""

    def __init__(self, select_script=(), confirm_script=()):
        self._select_script = list(select_script)
        self._confirm_script = list(confirm_script)
        self.select_calls = []
        self.notifications = []

    def has_capability(self, method):
        return True

    def notify(self, method, params):
        """通知记录（登录清理的 progress 清除等）。"""
        self.notifications.append((method, params))

    async def request(self, method, params):
        if method == "confirm":
            confirmed = self._confirm_script.pop(0) if self._confirm_script else True
            return SimpleNamespace(value=None, cancelled=False, confirmed=confirmed)
        assert method == "select"
        self.select_calls.append(params)
        if self._select_script:
            value = self._select_script.pop(0)
        else:
            # 结构化 items 优先（value 字段），字符串 options 兜底
            items = params.get("items") or []
            options = params.get("options") or []
            if items:
                value = items[0].get("value")
            else:
                value = options[0] if options else None
        return SimpleNamespace(value=value, cancelled=value is None, confirmed=None)

    def notify_message(self, message, type="info"):
        pass


def _handler(api, name):
    return api.commands[name]["handler"]


def test_extension_registers_all_commands():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    expected = {
        "help",
        "compact",
        "fork",
        "clone",
        "export",
        "import",
        "model",
        "scoped-models",
        "resume",
        "login",
        "logout",
        "session",
        "name",
        "new",
        "reload",
        "tree",
        "todos",
        "agent",
        "trust",
        "untrust",
    }
    assert set(api.commands.keys()) == expected


def test_compact_calls_context_compact():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(compact=AsyncMock())
    _run(_handler(api, "compact")("", ctx))
    ctx.compact.assert_awaited_once_with()


def test_compact_passes_instructions():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(compact=AsyncMock())
    _run(_handler(api, "compact")("summarize recent changes", ctx))
    ctx.compact.assert_awaited_once_with(
        {"custom_instructions": "summarize recent changes"}
    )


# -----------------------------------------------------------------------------
# /fork
# -----------------------------------------------------------------------------


def test_fork_parses_args():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(
        wait_for_idle=AsyncMock(),
        fork=AsyncMock(),
        append_entry=Mock(),
    )
    _run(_handler(api, "fork")("abc123 before", ctx))
    ctx.wait_for_idle.assert_awaited_once()
    ctx.fork.assert_awaited_once_with("abc123", position="before")


def test_fork_errors_without_entry_id_headless():
    """无参数 + 无 UI：退化回用法提示。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(
        has_ui=False,
        wait_for_idle=AsyncMock(),
        fork=AsyncMock(),
        append_entry=Mock(),
    )
    _run(_handler(api, "fork")("", ctx))
    ctx.fork.assert_not_awaited()
    ctx.append_entry.assert_called_once()


def _user_entry(entry_id, text):
    return SimpleNamespace(
        type="message",
        id=entry_id,
        message=SimpleNamespace(role="user", content=text),
    )


# ---------------------------------------------------------------------------
# /tree：会话树选择器（无参数链路）
# ---------------------------------------------------------------------------


def _tree_session_manager():
    """小树形：root → a → leaf（当前叶）；root → b 侧支。"""
    entries = [
        SimpleNamespace(
            type="message",
            id="root",
            parent_id=None,
            message=SimpleNamespace(role="user", content="根问题"),
        ),
        SimpleNamespace(
            type="message",
            id="a",
            parent_id="root",
            message=SimpleNamespace(role="assistant", content="回答一"),
        ),
        SimpleNamespace(
            type="message",
            id="leaf",
            parent_id="a",
            message=SimpleNamespace(role="user", content="追问"),
        ),
        SimpleNamespace(
            type="message",
            id="b",
            parent_id="root",
            message=SimpleNamespace(role="user", content="侧支问题"),
        ),
    ]
    children = {}
    for entry in entries:
        children.setdefault(entry.parent_id, []).append(entry)
    return SimpleNamespace(
        get_entries=lambda: entries,
        get_children=lambda parent_id: children.get(parent_id, []),
        get_leaf_id=lambda: "leaf",
        get_label=lambda entry_id: None,
    )


def test_tree_selector_flatten_with_depth_and_current():
    """无参数 + UI：DFS 扁平化 + depth 元信息 + current 路径标记，选中导航。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ui = _FakeUI(select_script=["b"])  # 选侧支节点
    ctx = SimpleNamespace(
        has_ui=True,
        ui=ui,
        session_manager=_tree_session_manager(),
        wait_for_idle=AsyncMock(),
        navigate_tree=AsyncMock(),
        append_entry=Mock(),
    )
    _run(_handler(api, "tree")("", ctx))

    items = ui.select_calls[0]["items"]
    # DFS 顺序：root(0) → a(1) → leaf(2) → b(1)
    assert [item["value"] for item in items] == ["root", "a", "leaf", "b"]
    assert [item["depth"] for item in items] == [0, 1, 2, 1]
    # current 路径：root/a/leaf（叶 → 根回溯），b 不在
    assert "current" in items[0]["description"]
    assert "current" not in items[3]["description"]
    # label 摘要：role + 预览
    assert "user" in items[0]["label"] and "根问题" in items[0]["label"]
    ctx.navigate_tree.assert_awaited_once_with("b")


def test_tree_selector_cancel_does_nothing():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ui = _FakeUI(select_script=[None])
    ctx = SimpleNamespace(
        has_ui=True,
        ui=ui,
        session_manager=_tree_session_manager(),
        wait_for_idle=AsyncMock(),
        navigate_tree=AsyncMock(),
        append_entry=Mock(),
    )
    _run(_handler(api, "tree")("", ctx))
    ctx.navigate_tree.assert_not_awaited()


def test_tree_headless_errors():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(
        has_ui=False,
        session_manager=None,
        wait_for_idle=AsyncMock(),
        navigate_tree=AsyncMock(),
        append_entry=Mock(),
    )
    _run(_handler(api, "tree")("", ctx))
    ctx.navigate_tree.assert_not_awaited()
    ctx.append_entry.assert_called_once()


def test_export_default_path():
    """无参数：默认导出 cwd 下 nova-session-<id前8>.jsonl。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(
        wait_for_idle=AsyncMock(),
        export=AsyncMock(
            return_value={"exported_to": "/abs/nova-session-abcd1234.jsonl"}
        ),
        get_session_info=lambda: {"id": "abcd1234-xyz"},
        append_entry=Mock(),
    )
    _run(_handler(api, "export")("", ctx))
    ctx.export.assert_awaited_once_with("nova-session-abcd1234.jsonl")


def test_export_with_path():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(
        wait_for_idle=AsyncMock(),
        export=AsyncMock(return_value={"exported_to": "/tmp/out.jsonl"}),
        get_session_info=lambda: {"id": "x"},
        append_entry=Mock(),
    )
    _run(_handler(api, "export")("/tmp/out.jsonl", ctx))
    ctx.export.assert_awaited_once_with("/tmp/out.jsonl")


def test_fork_selector_picks_user_message():
    """无参数 + UI：选择器列出用户消息，选中后按 position='at' 分叉。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    session_manager = SimpleNamespace(
        get_entries=lambda: [
            _user_entry("entry-old", "first question"),
            SimpleNamespace(
                type="message",
                id="entry-assistant",
                message=SimpleNamespace(role="assistant", content="answer"),
            ),
            _user_entry("entry-new", "second question"),
        ]
    )
    ui = _FakeUI()  # 默认选第一个选项（最新一条）
    ctx = SimpleNamespace(
        has_ui=True,
        ui=ui,
        session_manager=session_manager,
        wait_for_idle=AsyncMock(),
        fork=AsyncMock(),
        append_entry=Mock(),
    )
    _run(_handler(api, "fork")("", ctx))

    # 选择器条目：最新在前，且只含 user 消息
    items = ui.select_calls[0]["items"]
    assert len(items) == 2
    assert "second question" in items[0]["label"]
    assert "first question" in items[1]["label"]
    ctx.fork.assert_awaited_once_with("entry-new", position="at")


def test_fork_selector_cancel_does_nothing():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    session_manager = SimpleNamespace(
        get_entries=lambda: [_user_entry("entry-1", "hello")]
    )
    ui = _FakeUI(select_script=[None])
    ctx = SimpleNamespace(
        has_ui=True,
        ui=ui,
        session_manager=session_manager,
        wait_for_idle=AsyncMock(),
        fork=AsyncMock(),
        append_entry=Mock(),
    )
    _run(_handler(api, "fork")("", ctx))
    ctx.fork.assert_not_awaited()


def test_fork_selector_no_user_messages():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    session_manager = SimpleNamespace(get_entries=lambda: [])
    ctx = SimpleNamespace(
        has_ui=True,
        ui=_FakeUI(),
        session_manager=session_manager,
        wait_for_idle=AsyncMock(),
        fork=AsyncMock(),
        append_entry=Mock(),
    )
    _run(_handler(api, "fork")("", ctx))
    ctx.fork.assert_not_awaited()
    ctx.append_entry.assert_called_once()


# -----------------------------------------------------------------------------
# /model
# -----------------------------------------------------------------------------


def test_model_sets_model():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(set_model=AsyncMock())
    _run(_handler(api, "model")("openai/gpt-4o", ctx))
    ctx.set_model.assert_awaited_once_with("openai/gpt-4o")


def test_model_shows_current_when_no_args_headless():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    class _Model:
        provider = "openai"
        id = "gpt-4o"

    ctx = SimpleNamespace(
        has_ui=False,
        model_runtime=None,
        model=_Model(),
        append_entry=Mock(),
    )
    _run(_handler(api, "model")("", ctx))
    ctx.append_entry.assert_called_once()
    assert "openai/gpt-4o" in ctx.append_entry.call_args[0][1]["text"]


def test_model_selector_switches():
    """无参数 + UI：选择器列出可用模型并标记 current，选中即切换。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    models = [
        SimpleNamespace(provider="openai", id="gpt-4o"),
        SimpleNamespace(provider="volcengine", id="deepseek"),
    ]
    model_runtime = SimpleNamespace(get_available_snapshot=lambda: models)
    ui = _FakeUI()  # 默认选第一个
    ctx = SimpleNamespace(
        has_ui=True,
        ui=ui,
        model_runtime=model_runtime,
        model=models[1],
        set_model=AsyncMock(),
        append_entry=Mock(),
    )
    _run(_handler(api, "model")("", ctx))

    items = ui.select_calls[0]["items"]
    assert items[0]["value"] == "openai/gpt-4o"
    assert items[0]["label"] == "gpt-4o"
    assert "current" in items[1]["description"]
    # group 元信息（前端选择器按 provider 分段渲染组头）
    assert items[0]["group"] == "openai"
    assert items[1]["group"] == "volcengine"
    ctx.set_model.assert_awaited_once_with("openai/gpt-4o")


def test_model_selector_no_models():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    model_runtime = SimpleNamespace(get_available_snapshot=lambda: [])
    ctx = SimpleNamespace(
        has_ui=True,
        ui=_FakeUI(),
        model_runtime=model_runtime,
        model=None,
        set_model=AsyncMock(),
        append_entry=Mock(),
    )
    _run(_handler(api, "model")("", ctx))
    ctx.set_model.assert_not_awaited()
    ctx.append_entry.assert_called_once()


# -----------------------------------------------------------------------------
# /scoped-models（headless 文本回退；TUI 池面板在 frontend 段）
# -----------------------------------------------------------------------------


def _scoped_entry(provider, model_id, thinking_level=None):
    return SimpleNamespace(
        model=SimpleNamespace(provider=provider, id=model_id),
        thinking_level=thinking_level,
    )


def test_scoped_models_empty_pool():
    """空池：提示为空而非抛错。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(
        get_scoped_models=lambda: [],
        model=None,
        append_entry=Mock(),
    )
    _run(_handler(api, "scoped-models")("", ctx))
    assert "为空" in ctx.append_entry.call_args[0][1]["text"]


def test_scoped_models_lists_pool_in_order():
    """按循环顺序编号列出；thinking 级别与 current 标记随条目透出。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    scoped = [
        _scoped_entry("volcengine", "deepseek", "high"),
        _scoped_entry("openai", "gpt-4o"),
    ]
    ctx = SimpleNamespace(
        get_scoped_models=lambda: scoped,
        model=SimpleNamespace(provider="openai", id="gpt-4o"),
        append_entry=Mock(),
    )
    _run(_handler(api, "scoped-models")("", ctx))
    text = ctx.append_entry.call_args[0][1]["text"]
    assert text.index("1. volcengine/deepseek") < text.index("2. openai/gpt-4o")
    assert "thinking: high" in text
    assert "current" in text.splitlines()[-1]  # current 标记在 gpt-4o 行


# -----------------------------------------------------------------------------
# /resume
# -----------------------------------------------------------------------------


def _session_info(id_, name, path, modified=None, messages=3, first="hi"):
    return SimpleNamespace(
        id=id_,
        name=name,
        path=path,
        message_count=messages,
        first_message=first,
        modified=modified,
        created=None,
    )


def test_resume_selector_switches_session():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    sessions = [
        _session_info("sess-a", "alpha", "/tmp/a.jsonl", datetime(2026, 1, 1)),
        _session_info("sess-current", "now", "/tmp/c.jsonl"),
        _session_info("sess-b", None, "/tmp/b.jsonl", datetime(2026, 2, 1)),
    ]

    async def fake_list(dir_path):
        assert dir_path == "/sessions"
        return sessions

    module.list_sessions_from_dir = fake_list

    session_manager = SimpleNamespace(
        get_session_dir=lambda: "/sessions",
        get_session_id=lambda: "sess-current",
    )
    ui = _FakeUI()  # 默认选第一个
    ctx = SimpleNamespace(
        has_ui=True,
        ui=ui,
        session_manager=session_manager,
        wait_for_idle=AsyncMock(),
        switch_session=AsyncMock(),
        append_entry=Mock(),
    )
    _run(_handler(api, "resume")("", ctx))

    items = ui.select_calls[0]["items"]
    # 排除当前会话；按 modified 倒序（sess-b 最新在前）
    assert len(items) == 2
    assert "sess-b" in items[0]["label"]
    assert "alpha" in items[1]["label"]
    ctx.wait_for_idle.assert_awaited_once()
    ctx.switch_session.assert_awaited_once_with("/tmp/b.jsonl")


def test_resume_no_other_sessions():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    async def fake_list(dir_path):
        return [_session_info("sess-current", "now", "/tmp/c.jsonl")]

    module.list_sessions_from_dir = fake_list

    session_manager = SimpleNamespace(
        get_session_dir=lambda: "/sessions",
        get_session_id=lambda: "sess-current",
    )
    ctx = SimpleNamespace(
        has_ui=True,
        ui=_FakeUI(),
        session_manager=session_manager,
        wait_for_idle=AsyncMock(),
        switch_session=AsyncMock(),
        append_entry=Mock(),
    )
    _run(_handler(api, "resume")("", ctx))
    ctx.switch_session.assert_not_awaited()
    ctx.append_entry.assert_called_once()


def test_resume_headless_errors():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(
        has_ui=False,
        session_manager=None,
        switch_session=AsyncMock(),
        append_entry=Mock(),
    )
    _run(_handler(api, "resume")("", ctx))
    ctx.switch_session.assert_not_awaited()
    ctx.append_entry.assert_called_once()


# -----------------------------------------------------------------------------
# /login /logout
# -----------------------------------------------------------------------------


def _oauth_provider():
    """仅 OAuth 能力的 provider（model_runtime.get_provider 的 mock 返回）。"""
    return SimpleNamespace(
        auth=SimpleNamespace(
            oauth=SimpleNamespace(login=object(), login_label=None),
            api_key=None,
        )
    )


def _api_key_provider():
    """仅 API key 能力的 provider。"""
    return SimpleNamespace(
        auth=SimpleNamespace(
            oauth=None,
            api_key=SimpleNamespace(login=object()),
        )
    )


def _dual_provider():
    """OAuth + API key 双能力的 provider（如 kimi-coding）。"""
    return SimpleNamespace(
        auth=SimpleNamespace(
            oauth=SimpleNamespace(login=object(), login_label=None),
            api_key=SimpleNamespace(login=object()),
        )
    )


def test_login_with_provider_arg():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    model_runtime = SimpleNamespace(
        login=AsyncMock(return_value=SimpleNamespace(type="oauth")),
        get_all=lambda: [],
        get_available_snapshot=lambda: [],
        get_provider_auth_status=lambda provider: {"configured": False},
        get_provider=lambda provider: _oauth_provider(),
    )
    ctx = SimpleNamespace(
        has_ui=True,
        ui=_FakeUI(),
        get_signal=lambda: None,
        model_runtime=model_runtime,
        model=None,
        set_model=AsyncMock(return_value=True),
        append_entry=Mock(),
    )
    _run(_handler(api, "login")("kimi-coding", ctx))

    args, _ = model_runtime.login.await_args
    assert args[0] == "kimi-coding"
    assert args[1] == "oauth"
    text = ctx.append_entry.call_args[0][1]["text"]
    assert "已登录 kimi-coding" in text


def test_login_cancelled():
    from nova_harness.core.config.auth.interaction import LoginCancelledError

    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    model_runtime = SimpleNamespace(
        login=AsyncMock(side_effect=LoginCancelledError()),
        get_all=lambda: [],
        get_provider_auth_status=lambda provider: {"configured": False},
        get_provider=lambda provider: _oauth_provider(),
    )
    ctx = SimpleNamespace(
        has_ui=True,
        ui=_FakeUI(),
        get_signal=lambda: None,
        model_runtime=model_runtime,
        append_entry=Mock(),
    )
    _run(_handler(api, "login")("kimi-coding", ctx))
    # 取消反馈归前端（Esc 发起、即时可靠）——后端不再发"登录已取消"消息
    ctx.append_entry.assert_not_called()


def test_login_failure():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    model_runtime = SimpleNamespace(
        login=AsyncMock(side_effect=RuntimeError("no oauth support")),
        get_all=lambda: [],
        get_provider_auth_status=lambda provider: {"configured": False},
        get_provider=lambda provider: _oauth_provider(),
    )
    ctx = SimpleNamespace(
        has_ui=True,
        ui=_FakeUI(),
        get_signal=lambda: None,
        model_runtime=model_runtime,
        append_entry=Mock(),
    )
    _run(_handler(api, "login")("some-provider", ctx))
    text = ctx.append_entry.call_args[0][1]["text"]
    assert "登录失败" in text


def test_login_selector_picks_provider():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    models = [
        SimpleNamespace(provider="volcengine", id="deepseek"),
        SimpleNamespace(provider="kimi-coding", id="k2"),
    ]
    model_runtime = SimpleNamespace(
        login=AsyncMock(return_value=SimpleNamespace(type="oauth")),
        get_all=lambda: models,
        get_available_snapshot=lambda: [],
        get_provider_auth_status=lambda provider: {"configured": False},
        get_provider=lambda provider: _oauth_provider(),
    )
    ui = _FakeUI(select_script=["kimi-coding"])
    ctx = SimpleNamespace(
        has_ui=True,
        ui=ui,
        get_signal=lambda: None,
        model_runtime=model_runtime,
        model=None,
        set_model=AsyncMock(return_value=True),
        append_entry=Mock(),
    )
    _run(_handler(api, "login")("", ctx))

    # 候选为排序后的 provider 集合（select_items 结构化项）
    items = ui.select_calls[0]["items"]
    assert [item["value"] for item in items] == ["kimi-coding", "volcengine"]
    args, _ = model_runtime.login.await_args
    assert args[0] == "kimi-coding"


def test_login_api_key_only_provider_goes_straight():
    """api-key-only provider（如 volcengine）：不弹认证方式选择器，直进
    api_key 登录（此前 /login 写死 oauth，该路径报 does not support oauth）。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    model_runtime = SimpleNamespace(
        login=AsyncMock(return_value=SimpleNamespace(type="api_key")),
        get_all=lambda: [],
        get_available_snapshot=lambda: [],
        get_provider_auth_status=lambda provider: {"configured": False},
        get_provider=lambda provider: _api_key_provider(),
    )
    ui = _FakeUI()
    ctx = SimpleNamespace(
        has_ui=True,
        ui=ui,
        get_signal=lambda: None,
        model_runtime=model_runtime,
        model=None,
        set_model=AsyncMock(return_value=True),
        append_entry=Mock(),
    )
    _run(_handler(api, "login")("volcengine", ctx))

    args, _ = model_runtime.login.await_args
    assert args[0] == "volcengine"
    assert args[1] == "api_key"
    # 唯一能力直进：不再弹认证方式选择器（select 只可能是 provider 选择器，
    # 本用例给了 provider 参数，select 一次都不应发生）
    assert ui.select_calls == []


def test_login_dual_auth_prompts_method_choice():
    """双能力 provider（如 kimi-coding）：弹认证方式选择器，选 API key 即
    以 api_key 登录。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    model_runtime = SimpleNamespace(
        login=AsyncMock(return_value=SimpleNamespace(type="api_key")),
        get_all=lambda: [],
        get_available_snapshot=lambda: [],
        get_provider_auth_status=lambda provider: {"configured": False},
        get_provider=lambda provider: _dual_provider(),
    )
    ui = _FakeUI(select_script=["API key"])
    ctx = SimpleNamespace(
        has_ui=True,
        ui=ui,
        get_signal=lambda: None,
        model_runtime=model_runtime,
        model=None,
        set_model=AsyncMock(return_value=True),
        append_entry=Mock(),
    )
    _run(_handler(api, "login")("kimi-coding", ctx))

    # 第一次 select 即认证方式选择器（给了 provider 参数）
    options = ui.select_calls[0]["options"]
    assert "API key" in options
    args, _ = model_runtime.login.await_args
    assert args[0] == "kimi-coding"
    assert args[1] == "api_key"


def test_login_ambient_provider_rejected_with_guidance():
    """无任何 login 能力的 provider（ambient 鉴权）：报错并指引环境变量
    /models.json 配置，不进入登录流程。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    model_runtime = SimpleNamespace(
        login=AsyncMock(),
        get_all=lambda: [],
        get_provider_auth_status=lambda provider: {"configured": False},
        get_provider=lambda provider: SimpleNamespace(auth=None),
    )
    ctx = SimpleNamespace(
        has_ui=True,
        ui=_FakeUI(),
        get_signal=lambda: None,
        model_runtime=model_runtime,
        append_entry=Mock(),
    )
    _run(_handler(api, "login")("some-provider", ctx))

    text = ctx.append_entry.call_args[0][1]["text"]
    assert "ambient" in text or "环境变量" in text
    model_runtime.login.assert_not_called()


def test_login_selector_shows_auth_status_tags():
    """provider 选择器 description 带认证状态标签：

    已配置凭据 → ``✓ OAuth`` / ``✓ API key``（按当前解析类型）；环境变量
    可得 → ``env: <VAR名>``；未配置 → 无标签。
    """
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    models = [
        SimpleNamespace(provider="oauth-p", id="m0"),
        SimpleNamespace(provider="stored-p", id="m1"),
        SimpleNamespace(provider="env-p", id="m2"),
        SimpleNamespace(provider="plain-p", id="m3"),
    ]
    statuses = {
        "oauth-p": {"configured": True, "source": "stored"},
        "stored-p": {"configured": True, "source": "stored"},
        "env-p": {
            "configured": True,
            "source": "environment",
            "label": "ENV_P_API_KEY",
        },
        "plain-p": {"configured": False},
    }
    oauth_providers = {"oauth-p"}
    model_runtime = SimpleNamespace(
        login=AsyncMock(return_value=SimpleNamespace(type="oauth")),
        get_all=lambda: models,
        get_provider_auth_status=lambda provider: statuses[provider],
        is_using_oauth=lambda provider: provider in oauth_providers,
    )
    ui = _FakeUI(select_script=[None])  # 取消选择，只检查 items
    ctx = SimpleNamespace(
        has_ui=True,
        ui=ui,
        get_signal=lambda: None,
        model_runtime=model_runtime,
        append_entry=Mock(),
    )
    _run(_handler(api, "login")("", ctx))

    items = {item["value"]: item for item in ui.select_calls[0]["items"]}
    assert items["oauth-p"]["description"] == "✓ OAuth"
    assert items["stored-p"]["description"] == "✓ API key"
    assert items["env-p"]["description"] == "env: ENV_P_API_KEY"
    assert "description" not in items["plain-p"]


def test_login_headless_rejected_even_with_arg():
    """device code 流程无 prompt 关卡，headless 下直接拒绝启动（不轮询）。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(
        has_ui=False,
        model_runtime=SimpleNamespace(login=AsyncMock()),
        append_entry=Mock(),
    )
    _run(_handler(api, "login")("kimi-coding", ctx))
    ctx.model_runtime.login.assert_not_awaited()
    ctx.append_entry.assert_called_once()
    assert "需要 UI" in ctx.append_entry.call_args[0][1]["text"]


def test_logout_with_provider_arg():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    model_runtime = SimpleNamespace(
        logout=AsyncMock(),
        get_all=lambda: [],
        get_provider_auth_status=lambda provider: {
            "configured": True,
            "source": "stored",
        },
    )
    ctx = SimpleNamespace(
        has_ui=True,
        ui=_FakeUI(),
        model_runtime=model_runtime,
        append_entry=Mock(),
    )
    _run(_handler(api, "logout")("kimi-coding", ctx))
    model_runtime.logout.assert_awaited_once_with("kimi-coding")
    assert "已登出 kimi-coding" in ctx.append_entry.call_args[0][1]["text"]


def test_logout_failure():
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    model_runtime = SimpleNamespace(
        logout=AsyncMock(side_effect=RuntimeError("storage locked")),
        get_all=lambda: [],
        get_provider_auth_status=lambda provider: {
            "configured": True,
            "source": "stored",
        },
    )
    ctx = SimpleNamespace(
        has_ui=True,
        ui=_FakeUI(),
        model_runtime=model_runtime,
        append_entry=Mock(),
    )
    _run(_handler(api, "logout")("kimi-coding", ctx))
    assert "登出失败" in ctx.append_entry.call_args[0][1]["text"]


# -----------------------------------------------------------------------------
# /session 等既有命令
# -----------------------------------------------------------------------------


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
        append_entry=Mock(),
    )
    _run(_handler(api, "session")("", ctx))
    ctx.append_entry.assert_called_once()
    text = ctx.append_entry.call_args[0][1]["text"]
    assert "sess-1" in text
    assert "test" in text


# -----------------------------------------------------------------------------
# /todos（headless 文本回退；TUI 模态查看器在 frontend 段）
# -----------------------------------------------------------------------------


def _todo_entry(todos):
    return SimpleNamespace(
        type="message",
        message=SimpleNamespace(
            role="toolResult",
            tool_name="todo",
            details={"todos": todos},
        ),
    )


def test_todos_shows_latest_list_on_branch():
    """扫当前分支最新 todo 结果，文本清单含进度与图标。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    entries = [
        _todo_entry([{"content": "old", "status": "pending"}]),
        _todo_entry(
            [
                {"content": "写测试", "status": "completed"},
                {"content": "修 bug", "status": "in_progress"},
            ]
        ),
    ]
    ctx = SimpleNamespace(
        session_manager=SimpleNamespace(get_branch=lambda: entries),
        append_entry=Mock(),
    )
    _run(_handler(api, "todos")("", ctx))
    text = ctx.append_entry.call_args[0][1]["text"]
    assert "1/2 completed" in text
    assert "✓ 写测试" in text
    assert "◐ 修 bug" in text
    assert "old" not in text  # 旧快照被最新覆盖


def test_todos_empty_when_never_used():
    """从未有 todo 结果：提示创建而非抛错。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(
        session_manager=SimpleNamespace(get_branch=lambda: []),
        append_entry=Mock(),
    )
    _run(_handler(api, "todos")("", ctx))
    assert "还没有 todo 清单" in ctx.append_entry.call_args[0][1]["text"]


def test_todos_empty_list_snapshot():
    """空清单（已清空）是合法快照。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(
        session_manager=SimpleNamespace(get_branch=lambda: [_todo_entry([])]),
        append_entry=Mock(),
    )
    _run(_handler(api, "todos")("", ctx))
    assert "已清空" in ctx.append_entry.call_args[0][1]["text"]


def test_help_lists_all_commands_sorted():
    """/help 按调用名排序列出全部已注册命令与描述。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    commands = [
        SimpleNamespace(name="todos", description="查看清单"),
        SimpleNamespace(name="compact", description="压缩上下文"),
        SimpleNamespace(name="help", description=None),
    ]
    ctx = SimpleNamespace(
        get_commands=lambda: commands,
        append_entry=Mock(),
    )
    _run(_handler(api, "help")("", ctx))
    text = ctx.append_entry.call_args[0][1]["text"]
    lines = text.splitlines()
    # 排序：compact < help < todos；无描述给占位
    assert lines[0].startswith("/compact")
    assert "压缩上下文" in lines[0]
    assert lines[1].startswith("/help")
    assert "(无描述)" in lines[1]
    assert lines[2].startswith("/todos")
    # 名称列对齐（等宽）
    assert lines[0].index("压") == lines[1].index("(") == lines[2].index("查")


def test_help_empty_registry():
    """无已注册命令时给出明确提示而非空输出。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    ctx = SimpleNamespace(
        get_commands=lambda: [],
        append_entry=Mock(),
    )
    _run(_handler(api, "help")("", ctx))
    assert "没有已注册的命令" in ctx.append_entry.call_args[0][1]["text"]


def test_login_configured_provider_requires_confirm():
    """已配置凭据的 provider 重登前弹确认；选否不触发登录。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    model_runtime = SimpleNamespace(
        login=AsyncMock(),
        get_all=lambda: [],
        get_provider_auth_status=lambda provider: {
            "configured": True,
            "source": "stored",
        },
        get_provider=lambda provider: _oauth_provider(),
    )
    ui = _FakeUI(confirm_script=[False])  # 用户选否
    ctx = SimpleNamespace(
        has_ui=True,
        ui=ui,
        get_signal=lambda: None,
        model_runtime=model_runtime,
        append_entry=Mock(),
    )
    _run(_handler(api, "login")("kimi-coding", ctx))
    model_runtime.login.assert_not_awaited()


def test_login_configured_provider_confirm_yes_proceeds():
    """确认覆盖后正常走登录流程。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    model_runtime = SimpleNamespace(
        login=AsyncMock(return_value=SimpleNamespace(type="oauth")),
        get_all=lambda: [],
        get_available_snapshot=lambda: [],
        get_provider_auth_status=lambda provider: {
            "configured": True,
            "source": "stored",
        },
        get_provider=lambda provider: _oauth_provider(),
    )
    ui = _FakeUI(confirm_script=[True])
    ctx = SimpleNamespace(
        has_ui=True,
        ui=ui,
        get_signal=lambda: None,
        model_runtime=model_runtime,
        model=None,
        set_model=AsyncMock(return_value=True),
        append_entry=Mock(),
    )
    _run(_handler(api, "login")("kimi-coding", ctx))
    model_runtime.login.assert_awaited_once()
    assert "已登录 kimi-coding" in ctx.append_entry.call_args[0][1]["text"]


def test_login_auto_selects_default_model_when_session_has_none():
    """登录闭环：会话无模型时自动选中该 provider 的默认模型
    （DEFAULT_MODEL_PER_PROVIDER 优先）——首启引导"登录即可对话"。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    available = [
        SimpleNamespace(provider="kimi-coding", id="other-model"),
        SimpleNamespace(provider="kimi-coding", id="kimi-for-coding"),
        SimpleNamespace(provider="volcengine", id="deepseek-v4-flash"),
    ]
    model_runtime = SimpleNamespace(
        login=AsyncMock(return_value=SimpleNamespace(type="oauth")),
        get_all=lambda: available,
        get_available_snapshot=lambda: available,
        get_provider_auth_status=lambda provider: {"configured": False},
        get_provider=lambda provider: _oauth_provider(),
    )
    ctx = SimpleNamespace(
        has_ui=True,
        ui=_FakeUI(),
        get_signal=lambda: None,
        model_runtime=model_runtime,
        model=None,
        set_model=AsyncMock(return_value=True),
        append_entry=Mock(),
    )
    _run(_handler(api, "login")("kimi-coding", ctx))

    target = ctx.set_model.await_args[0][0]
    assert target.provider == "kimi-coding"
    assert target.id == "kimi-for-coding"  # DEFAULT_MODEL_PER_PROVIDER 优先


def test_login_keeps_current_model_when_already_selected():
    """会话已有模型时登录不夺权（自动选中只在模型悬空时发生）。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    model_runtime = SimpleNamespace(
        login=AsyncMock(return_value=SimpleNamespace(type="oauth")),
        get_all=lambda: [],
        get_available_snapshot=lambda: [
            SimpleNamespace(provider="kimi-coding", id="kimi-for-coding")
        ],
        get_provider_auth_status=lambda provider: {"configured": False},
        get_provider=lambda provider: _oauth_provider(),
    )
    ctx = SimpleNamespace(
        has_ui=True,
        ui=_FakeUI(),
        get_signal=lambda: None,
        model_runtime=model_runtime,
        model=SimpleNamespace(provider="volcengine", id="deepseek-v4-flash"),
        set_model=AsyncMock(return_value=True),
        append_entry=Mock(),
    )
    _run(_handler(api, "login")("kimi-coding", ctx))

    ctx.set_model.assert_not_called()


def test_logout_unconfigured_provider_noop_with_notice():
    """未配置凭据的 provider：提示无需登出，不调 delete。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    model_runtime = SimpleNamespace(
        logout=AsyncMock(),
        get_all=lambda: [],
        get_provider_auth_status=lambda provider: {"configured": False},
        get_provider=lambda provider: _oauth_provider(),
    )
    ctx = SimpleNamespace(
        has_ui=True,
        ui=_FakeUI(),
        model_runtime=model_runtime,
        append_entry=Mock(),
    )
    _run(_handler(api, "logout")("ghost-provider", ctx))
    model_runtime.logout.assert_not_awaited()
    assert "未配置凭据" in ctx.append_entry.call_args[0][1]["text"]


def test_logout_env_sourced_credential_refused():
    """凭据来自环境变量/models.json（不在 auth.json）：拒绝登出并给指引。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)

    model_runtime = SimpleNamespace(
        logout=AsyncMock(),
        get_all=lambda: [],
        get_provider_auth_status=lambda provider: {
            "configured": True,
            "source": "environment",
            "label": "VOLCENGINE_API_KEY",
        },
    )
    ctx = SimpleNamespace(
        has_ui=True,
        ui=_FakeUI(),
        model_runtime=model_runtime,
        append_entry=Mock(),
    )
    _run(_handler(api, "logout")("volcengine", ctx))
    model_runtime.logout.assert_not_awaited()
    text = ctx.append_entry.call_args[0][1]["text"]
    assert "VOLCENGINE_API_KEY" in text and "无法" in text
