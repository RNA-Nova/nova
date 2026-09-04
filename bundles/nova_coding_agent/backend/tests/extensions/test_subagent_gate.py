"""subagent_gate 扩展的单元测试（自治权检查点）。

覆盖：
- 非 subagent 工具 / 无 agent 名：零拦截；
- headless（无 UI）：直接放行（确认是有 UI 时的增强，不是新门槛）；
- 允许一次：本次放行，下次仍问；
- 本会话始终允许：写允许集 + ``subagent_allow`` 条目持久化（累计全集），
  下次不问；session_start/session_tree 从分支最新条目恢复（替换语义）；
- 取消（含选择器 Esc）：block 拦截，reason 回给 LLM；
- 多 agent 调用（parallel/chain）逐名裁决：已在允许集的跳过不问，
  任一取消即整体拦截。
"""

import asyncio
import importlib.util
import os
from types import SimpleNamespace


def _load_extension():
    """动态加载 subagent_gate extension 模块。"""
    ext_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "extensions", "subagent_gate.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_test_subagent_gate_extension", ext_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(coro):
    return asyncio.run(coro)


class _FakeNovaAPI:
    """模拟 NovaExtensionAPI，捕获 on() 注册的 handler（同事件多 handler 保序）。"""

    def __init__(self):
        self.handlers = {}

    def on(self, event_type, handler):
        self.handlers.setdefault(event_type, []).append(handler)


class _FakeUI:
    """模拟泛型 UIContext：select 按脚本返回（None = 用户取消）。"""

    def __init__(self, select_script=()):
        self._select_script = list(select_script)
        self.select_calls = []

    def has_capability(self, method):
        return True

    async def request(self, method, params):
        assert method == "select"
        self.select_calls.append(params)
        value = self._select_script.pop(0) if self._select_script else None
        return SimpleNamespace(value=value, cancelled=value is None, confirmed=None)

    def notify(self, method, params):
        pass


def _allow_entry(*agents: str):
    return SimpleNamespace(
        type="custom", custom_type="subagent_allow", data={"agents": list(agents)}
    )


class _FakeSessionManager:
    """最小会话管理器假件：只承载分支条目扫描。"""

    def __init__(self, branch=()):
        self._branch = list(branch)

    def get_branch(self):
        return list(self._branch)


def _make_ctx(ui=None, has_ui=True, branch=()):
    """构造 handler ctx 假件（append_entry 记录 + 分支条目扫描）。"""
    ctx = SimpleNamespace(
        has_ui=has_ui,
        ui=ui or _FakeUI(),
        session_manager=_FakeSessionManager(branch),
        _appended=[],
    )
    ctx.append_entry = lambda t, data: ctx._appended.append((t, data))
    return ctx


def _load():
    """加载扩展并注册，返回 (api, handlers)。"""
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    return api


def _event(args):
    return SimpleNamespace(tool_name="subagent", args=args)


# -----------------------------------------------------------------------------
# 零拦截面
# -----------------------------------------------------------------------------


def test_non_subagent_tool_passes():
    api = _load()
    handler = api.handlers["tool_call"][0]
    ctx = _make_ctx()
    assert _run(handler(SimpleNamespace(tool_name="bash", args={}), ctx)) is None


def test_no_agent_names_passes():
    """参数无 agent 名（畸形调用——模式校验归工具）：零拦截。"""
    api = _load()
    handler = api.handlers["tool_call"][0]
    ctx = _make_ctx()
    assert _run(handler(_event({}), ctx)) is None


def test_headless_passes_without_asking():
    """无 UI 直接放行（headless 不设卡）。"""
    api = _load()
    handler = api.handlers["tool_call"][0]
    ui = _FakeUI()
    ctx = _make_ctx(ui=ui, has_ui=False)
    assert _run(handler(_event({"agent": "scout", "task": "t"}), ctx)) is None
    assert ui.select_calls == []  # 未弹选择器


# -----------------------------------------------------------------------------
# 逐名裁决
# -----------------------------------------------------------------------------


def test_allow_once_asks_again_next_time():
    """允许一次：本次放行，下次仍问。"""
    api = _load()
    handler = api.handlers["tool_call"][0]
    ui = _FakeUI(select_script=["允许一次", "允许一次"])
    ctx = _make_ctx(ui=ui)

    event = _event({"agent": "scout", "task": "t"})
    assert _run(handler(event, ctx)) is None
    assert _run(handler(event, ctx)) is None
    assert len(ui.select_calls) == 2  # 两次都问了
    assert ctx._appended == []  # 允许一次不落条目


def test_always_persists_and_skips_next_time():
    """本会话始终允许：写允许集 + 条目持久化（累计全集），下次不问。"""
    api = _load()
    handler = api.handlers["tool_call"][0]
    ui = _FakeUI(select_script=["本会话始终允许", "本会话始终允许"])
    ctx = _make_ctx(ui=ui)

    assert _run(handler(_event({"agent": "scout", "task": "t"}), ctx)) is None
    assert ctx._appended == [("subagent_allow", {"agents": ["scout"]})]

    # 第二个 agent always：条目为合并去重后的累计全集
    assert _run(handler(_event({"agent": "worker", "task": "t"}), ctx)) is None
    assert ctx._appended[-1] == ("subagent_allow", {"agents": ["scout", "worker"]})

    # 已允许集内：不再弹选择器
    assert _run(handler(_event({"agent": "scout", "task": "t2"}), ctx)) is None
    assert len(ui.select_calls) == 2


def test_cancel_blocks_with_reason():
    """取消（选“取消”或 Esc）：block 拦截，reason 指明 agent。"""
    api = _load()
    handler = api.handlers["tool_call"][0]
    for script in (["取消"], [None]):  # 显式取消 / Esc（value=None）
        ui = _FakeUI(select_script=script)
        ctx = _make_ctx(ui=ui)
        result = _run(handler(_event({"agent": "scout", "task": "t"}), ctx))
        assert result is not None
        assert result.block is True
        assert "scout" in result.reason


def test_parallel_adjudicates_per_agent():
    """parallel：逐名裁决——已在允许集的跳过不问，新名逐个问。"""
    api = _load()
    handler = api.handlers["tool_call"][0]
    ui = _FakeUI(select_script=["本会话始终允许", "允许一次"])
    ctx = _make_ctx(ui=ui)

    tasks = {
        "tasks": [
            {"agent": "scout", "task": "a"},
            {"agent": "worker", "task": "b"},
        ]
    }
    assert _run(handler(_event(tasks), ctx)) is None
    assert len(ui.select_calls) == 2  # 两个新名都问了

    # 第二轮：scout 已 always（不问）、worker 只允一次（再问）——脚本给取消
    # 注意：允许集挂在扩展实例闭包上——同一 a handler 共享
    ui2 = _FakeUI(select_script=["取消"])
    ctx2 = _make_ctx(ui=ui2)
    result = _run(handler(_event(tasks), ctx2))
    assert result is not None
    assert result.block is True
    assert "worker" in result.reason
    assert len(ui2.select_calls) == 1  # scout 在允许集内未问


def test_chain_cancel_blocks_whole_call():
    """chain：任一 agent 取消即整体拦截（已问过的允许仍然生效）。"""
    api = _load()
    handler = api.handlers["tool_call"][0]
    ui = _FakeUI(select_script=["允许一次", "取消"])
    ctx = _make_ctx(ui=ui)

    chain = {
        "chain": [
            {"agent": "scout", "task": "a"},
            {"agent": "planner", "task": "b {previous}"},
        ]
    }
    result = _run(handler(_event(chain), ctx))
    assert result is not None
    assert result.block is True
    assert "planner" in result.reason
    assert len(ui.select_calls) == 2  # scout 问过且放行，planner 被取消


def test_extract_dedupes_repeated_names():
    """同一调用中重复出现的 agent 名只问一次（去重保序）。"""
    api = _load()
    handler = api.handlers["tool_call"][0]
    ui = _FakeUI(select_script=["允许一次"])
    ctx = _make_ctx(ui=ui)

    tasks = {
        "tasks": [
            {"agent": "scout", "task": "a"},
            {"agent": "scout", "task": "b"},
        ]
    }
    assert _run(handler(_event(tasks), ctx)) is None
    assert len(ui.select_calls) == 1


# -----------------------------------------------------------------------------
# 分支恢复（session_start / session_tree——替换语义）
# -----------------------------------------------------------------------------


def test_session_start_restores_allowed_set():
    """session_start：分支最新 subagent_allow 条目恢复允许集，恢复后不再问。"""
    api = _load()
    handler = api.handlers["tool_call"][0]
    restore = api.handlers["session_start"][0]
    ctx = _make_ctx(branch=[_allow_entry("scout", "worker")])

    _run(restore(SimpleNamespace(), ctx))

    ui = _FakeUI()  # 空脚本——若被问会按取消处理
    ctx.ui = ui
    event = _event(
        {"tasks": [{"agent": "scout", "task": "a"}, {"agent": "worker", "task": "b"}]}
    )
    assert _run(handler(event, ctx)) is None
    assert ui.select_calls == []


def test_restore_uses_latest_entry():
    """分支恢复取最新一条（替换语义——旧条目不叠加）。"""
    api = _load()
    handler = api.handlers["tool_call"][0]
    restore = api.handlers["session_tree"][0]
    branch = [_allow_entry("scout", "worker"), _allow_entry("scout")]
    ctx = _make_ctx(branch=branch)

    _run(restore(SimpleNamespace(), ctx))

    # 最新条目只有 scout：worker 要被重新询问
    ui = _FakeUI(select_script=["允许一次"])
    ctx.ui = ui
    event = _event(
        {"tasks": [{"agent": "scout", "task": "a"}, {"agent": "worker", "task": "b"}]}
    )
    assert _run(handler(event, ctx)) is None
    assert len(ui.select_calls) == 1  # 只问了 worker


def test_restore_without_entry_keeps_current_set():
    """分支无条目：不动当前允许集。"""
    api = _load()
    handler = api.handlers["tool_call"][0]
    restore = api.handlers["session_start"][0]

    ui = _FakeUI(select_script=["本会话始终允许"])
    ctx = _make_ctx(ui=ui)
    assert _run(handler(_event({"agent": "scout", "task": "t"}), ctx)) is None

    # 换到无条目分支（session_tree 恢复）：允许集保持
    ctx2 = _make_ctx(branch=[])
    _run(restore(SimpleNamespace(), ctx2))
    ui2 = _FakeUI()
    ctx2.ui = ui2
    assert _run(handler(_event({"agent": "scout", "task": "t2"}), ctx2)) is None
    assert ui2.select_calls == []
