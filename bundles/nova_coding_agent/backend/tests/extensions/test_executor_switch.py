"""executor_switch 扩展测试（/executor 命令 + 条目持久化 + 分支恢复）。

覆盖：直切 local/远程（名字查端点清单）、选择器切换、notice 回执、
条目持久化、分支恢复（session_start 从最新条目还原）、refresh 触发。
"""

import asyncio
import importlib.util
import os

from nova_harness.types.ui.primitives import UIResponse

from nova_coding_agent.executor import get_backend_selection, reset_backend_selection


def _load_extension():
    ext_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "extensions", "executor_switch.py"
    )
    spec = importlib.util.spec_from_file_location("_test_executor_switch_ext", ext_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeNovaAPI:
    """捕获 on() 与 register_command() 注册（签名对齐真实 NovaExtensionAPI）。"""

    def __init__(self):
        self.handlers = {}
        self.commands = {}

    def on(self, event_type, handler):
        self.handlers.setdefault(event_type, []).append(handler)

    def register_command(self, name, options=None, **_kwargs):
        opts = options or {}
        self.commands[name] = opts["handler"]


class _FakeUI:
    """泛型 UIContext：select/input/dialog 按脚本应答；notify 记录。"""

    def __init__(self, select_script=(), input_script=(), dialog_script=()):
        self._scripts = {
            "select": list(select_script),
            "input": list(input_script),
            "dialog:interactive-shell": list(dialog_script),
        }
        self.notices = []

    def has_capability(self, method):
        return True

    async def request(self, method, params):
        script = self._scripts.get(method, [])
        value = script.pop(0) if script else None
        if method == "dialog:interactive-shell":
            # 真实现返回 {"exitCode": int}
            return UIResponse(
                value={"exitCode": value} if isinstance(value, int) else None,
                cancelled=value is None,
            )
        return UIResponse(value=value, cancelled=value is None)

    def notify(self, method, params):
        self.notices.append((method, params))


class _FakeSessionManager:
    def __init__(self, entries=()):
        self._entries = list(entries)

    def get_branch(self):
        return list(self._entries)


class _Entry:
    def __init__(self, data):
        self.type = "custom"
        self.custom_type = "executor_backend"
        self.data = data


class _FakeCtx:
    def __init__(self, ui, session_manager=None, executor_settings=None):
        self.ui = ui
        self.has_ui = True
        self.cwd = "/tmp"
        self.session_manager = session_manager
        self._executor_settings = executor_settings
        self.entries = []
        self.refresh_calls = 0
        self.registered = []
        self.unregistered = []

    def append_entry(self, entry_type, data):
        self.entries.append((entry_type, data))

    def get_executor_settings(self):
        return self._executor_settings

    def register_executor_endpoint(self, name, url, cwd=None):
        self.registered.append((name, url, cwd))

    def unregister_executor_endpoint(self, name):
        self.unregistered.append(name)
        return True

    def refresh_system_prompt(self):
        self.refresh_calls += 1


class _FakeSshHandle:
    """假 SSH 句柄（供给后扩展读远程家目录/shell 定 cwd 用）。"""

    default_cwd = "/home/alice"
    remote_shell = "bash"


class _FakeManager:
    """假 ExecutorClientManager：provision_ssh 记录调用并按脚本成败。"""

    def __init__(self, fail_with=None):
        self.calls = []
        self._fail_with = fail_with

    async def provision_ssh(self, target, on_progress=None, bootstrap=None):
        self.calls.append(
            {"target": target, "on_progress": on_progress, "bootstrap": bootstrap}
        )
        if on_progress is not None:
            on_progress("探测中…")
        if self._fail_with is not None:
            raise self._fail_with
        return object()  # 假 ExecutorClient

    def get_ssh_handle(self, target):
        return _FakeSshHandle()


def _notice_texts(ui):
    return [
        params.get("message", "") for method, params in ui.notices if method == "notify"
    ]


def _setup():
    reset_backend_selection()
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    return api


def _teardown():
    reset_backend_selection()


def test_switch_local_direct():
    api = _setup()
    try:
        ctx = _FakeCtx(_FakeUI())
        asyncio.run(api.commands["executor"]("local", ctx))
        assert get_backend_selection(None).backend == "local"
        assert ctx.entries == [("executor_backend", {"backend": "local", "url": None})]
        assert ctx.refresh_calls == 1
        assert ctx.ui.notices, "切换应有 notice 回执"
    finally:
        _teardown()


def test_switch_remote_by_url():
    api = _setup()
    try:
        ctx = _FakeCtx(_FakeUI())
        asyncio.run(api.commands["executor"]("remote wss://gpu-01:8080", ctx))
        sel = get_backend_selection(None)
        assert sel.backend == "executor" and sel.url == "wss://gpu-01:8080"
    finally:
        _teardown()


def test_switch_remote_by_endpoint_name():
    class _Endpoint:
        def __init__(self, name, url):
            self.name = name
            self.url = url

    class _Settings:
        default_backend = None
        endpoints = [_Endpoint("gpu-01", "wss://gpu-01:8080")]

    api = _setup()
    try:
        ctx = _FakeCtx(_FakeUI(), executor_settings=_Settings())
        asyncio.run(api.commands["executor"]("remote gpu-01", ctx))
        sel = get_backend_selection(None)
        assert sel.url == "wss://gpu-01:8080"
    finally:
        _teardown()


def test_selector_cancel_keeps_current():
    api = _setup()
    try:
        ctx = _FakeCtx(_FakeUI(select_script=[None]))  # 用户取消
        before = get_backend_selection(None).backend
        asyncio.run(api.commands["executor"]("", ctx))
        assert get_backend_selection(None).backend == before
        assert ctx.entries == []
    finally:
        _teardown()


def test_selector_pick_executor_local():
    api = _setup()
    try:
        ctx = _FakeCtx(_FakeUI(select_script=["executor-local"]))
        asyncio.run(api.commands["executor"]("", ctx))
        sel = get_backend_selection(None)
        assert sel.backend == "executor" and sel.url is None
    finally:
        _teardown()


def test_restore_from_branch_entry():
    api = _setup()
    try:
        sm = _FakeSessionManager(
            [_Entry({"backend": "executor", "url": "wss://gpu-02:9000"})]
        )
        ctx = _FakeCtx(_FakeUI(), session_manager=sm)
        handler = api.handlers["session_start"][0]
        handler(None, ctx)  # 同步 handler（恢复逻辑无 await）
        sel = get_backend_selection(None)
        assert sel.backend == "executor" and sel.url == "wss://gpu-02:9000"
        assert ctx.refresh_calls == 1
    finally:
        _teardown()


# ---------------------------------------------------------------------------
# SSH 远程：裸目标供给 + 自动登记 + forget
# ---------------------------------------------------------------------------


def _setup_ssh(fail_with=None, remote_exec_result=None):
    reset_backend_selection()
    module = _load_extension()
    api = _FakeNovaAPI()
    module.extension(api)
    manager = _FakeManager(fail_with=fail_with)
    module.get_executor_manager = lambda: manager
    # 远程准备命令（test -d / mkdir -p）走假执行：记录命令、按脚本给退出码
    remote_calls = []

    class _Result:
        def __init__(self):
            self.exit_code = (remote_exec_result or {}).get("exit_code", 0)
            self.output = (remote_exec_result or {}).get("output", "")

    async def _fake_remote_exec(_manager, url, command):
        remote_calls.append((url, command))
        return _Result()

    module._remote_exec = _fake_remote_exec
    return api, manager, remote_calls


def test_ssh_bare_target_provisions_registers_switches():
    api, manager, remote_calls = _setup_ssh()
    try:
        ctx = _FakeCtx(_FakeUI())
        asyncio.run(api.commands["executor"]("remote alice@gpu-01", ctx))
        # 供给：解析为 SshTarget，带进度与 bootstrap（FakeUI 全能力）
        assert len(manager.calls) == 1
        call = manager.calls[0]
        assert call["target"].ssh_dest == "alice@gpu-01"
        assert call["on_progress"] is not None
        assert call["bootstrap"] is not None
        # 缺省远程 cwd = 会话隔离工作区（假家目录 /home/alice + 会话 id default），
        # 经远程 mkdir -p 落实
        expected_cwd = "/home/alice/.nova/agent/executor/workspaces/default"
        assert remote_calls == [("ssh://alice@gpu-01", f"mkdir -p {expected_cwd}")]
        # 自动登记（host 作缺省名；缺省工作区不记忆目录）+ 选择翻转 + 条目持久化
        assert ctx.registered == [("gpu-01", "ssh://alice@gpu-01", None)]
        sel = get_backend_selection(None)
        assert sel.backend == "executor"
        assert sel.url == "ssh://alice@gpu-01"
        assert sel.remote_cwd == expected_cwd
        assert ctx.entries == [
            (
                "executor_backend",
                {
                    "backend": "executor",
                    "url": "ssh://alice@gpu-01",
                    "remote_cwd": expected_cwd,
                    "remote_shell": "bash",
                    "remote_home": "/home/alice",
                },
            )
        ]
        assert ctx.refresh_calls == 1
        texts = _notice_texts(ctx.ui)
        assert any("已登记端点 gpu-01" in text for text in texts)
        assert any("执行后端已切换" in text for text in texts)
    finally:
        _teardown()


def test_ssh_provision_failure_keeps_current():
    from nova_coding_agent.executor import ProvisionError

    api, manager, _rc = _setup_ssh(
        fail_with=ProvisionError("connect", "SSH 连接失败：refused")
    )
    try:
        ctx = _FakeCtx(_FakeUI())
        asyncio.run(api.commands["executor"]("remote alice@gpu-01", ctx))
        assert len(manager.calls) == 1
        # 失败不切换、不登记、不写条目
        assert ctx.registered == []
        assert ctx.entries == []
        assert get_backend_selection(None).backend == "local"
        texts = _notice_texts(ctx.ui)
        assert any("远程供给失败（connect）" in text for text in texts)
    finally:
        _teardown()


def test_ssh_explicit_cwd_validated_and_remembered():
    api, manager, remote_calls = _setup_ssh()
    try:
        ctx = _FakeCtx(_FakeUI())
        asyncio.run(api.commands["executor"]("remote alice@gpu-01 /data/proj", ctx))
        # 显式目录：test -d 校验（不建目录）
        assert remote_calls == [("ssh://alice@gpu-01", "test -d /data/proj")]
        sel = get_backend_selection(None)
        assert sel.remote_cwd == "/data/proj"
        # 显式目录随端点记忆
        assert ctx.registered == [("gpu-01", "ssh://alice@gpu-01", "/data/proj")]
        assert ctx.entries[0][1]["remote_cwd"] == "/data/proj"
    finally:
        _teardown()


def test_ssh_explicit_cwd_tilde_normalized():
    api, manager, remote_calls = _setup_ssh()
    try:
        ctx = _FakeCtx(_FakeUI())
        asyncio.run(api.commands["executor"]("remote alice@gpu-01 ~/work", ctx))
        # ~ 归一到远程家目录（executor 的 file:// cwd 不做 tilde 展开）
        assert remote_calls == [("ssh://alice@gpu-01", "test -d /home/alice/work")]
        assert get_backend_selection(None).remote_cwd == "/home/alice/work"
    finally:
        _teardown()


def test_ssh_explicit_cwd_missing_keeps_current():
    api, manager, _rc = _setup_ssh(remote_exec_result={"exit_code": 1})
    try:
        ctx = _FakeCtx(_FakeUI())
        asyncio.run(api.commands["executor"]("remote alice@gpu-01 /nope", ctx))
        # 目录不存在：不切换、不登记、不写条目
        assert get_backend_selection(None).backend == "local"
        assert ctx.registered == []
        assert ctx.entries == []
        texts = _notice_texts(ctx.ui)
        assert any("远程目录不存在" in text for text in texts)
    finally:
        _teardown()


def test_ssh_registered_endpoint_by_name_skips_register():
    class _Endpoint:
        def __init__(self, name, url, cwd=None):
            self.name = name
            self.url = url
            self.cwd = cwd

    class _Settings:
        default_backend = None
        endpoints = [_Endpoint("gpu", "ssh://alice@gpu-01", cwd="/data/remembered")]

    api, manager, remote_calls = _setup_ssh()
    try:
        ctx = _FakeCtx(_FakeUI(), executor_settings=_Settings())
        asyncio.run(api.commands["executor"]("remote gpu", ctx))
        assert len(manager.calls) == 1
        assert manager.calls[0]["target"].ssh_dest == "alice@gpu-01"
        assert ctx.registered == []  # 已登记端点不重复登记
        # 端点记住的目录被采用
        sel = get_backend_selection(None)
        assert sel.url == "ssh://alice@gpu-01"
        assert sel.remote_cwd == "/data/remembered"
        assert remote_calls == [("ssh://alice@gpu-01", "test -d /data/remembered")]
    finally:
        _teardown()


def test_ssh_scheme_url_registers():
    api, manager, _rc = _setup_ssh()
    try:
        ctx = _FakeCtx(_FakeUI())
        asyncio.run(api.commands["executor"]("remote ssh://bob@10.0.0.2:2222", ctx))
        call = manager.calls[0]
        assert (call["target"].user, call["target"].host, call["target"].port) == (
            "bob",
            "10.0.0.2",
            2222,
        )
        assert ctx.registered == [("10.0.0.2", "ssh://bob@10.0.0.2:2222", None)]
    finally:
        _teardown()


def test_selector_add_remote_flow():
    api, manager, _rc = _setup_ssh()
    try:
        ui = _FakeUI(
            select_script=["__add_remote__"], input_script=["carol@gpu-02", ""]
        )
        ctx = _FakeCtx(ui)
        asyncio.run(api.commands["executor"]("", ctx))
        assert manager.calls[0]["target"].ssh_dest == "carol@gpu-02"
        assert ctx.registered == [("gpu-02", "ssh://carol@gpu-02", None)]
    finally:
        _teardown()


def test_selector_add_remote_with_path():
    api, manager, remote_calls = _setup_ssh()
    try:
        ui = _FakeUI(
            select_script=["__add_remote__"],
            input_script=["carol@gpu-02", "/srv/ml"],
        )
        ctx = _FakeCtx(ui)
        asyncio.run(api.commands["executor"]("", ctx))
        assert remote_calls == [("ssh://carol@gpu-02", "test -d /srv/ml")]
        assert ctx.registered == [("gpu-02", "ssh://carol@gpu-02", "/srv/ml")]
        assert get_backend_selection(None).remote_cwd == "/srv/ml"
    finally:
        _teardown()


def test_selector_add_remote_cancel_input():
    api, manager, _rc = _setup_ssh()
    try:
        ui = _FakeUI(select_script=["__add_remote__"], input_script=[None])
        ctx = _FakeCtx(ui)
        asyncio.run(api.commands["executor"]("", ctx))
        assert manager.calls == []
        assert ctx.entries == []
    finally:
        _teardown()


def test_forget_endpoint():
    api, manager, _rc = _setup_ssh()
    try:
        ctx = _FakeCtx(_FakeUI())
        asyncio.run(api.commands["executor"]("forget gpu-01", ctx))
        assert ctx.unregistered == ["gpu-01"]
        texts = _notice_texts(ctx.ui)
        assert any("已移除端点：gpu-01" in text for text in texts)
    finally:
        _teardown()


def test_ws_url_still_direct_without_provision():
    api, manager, _rc = _setup_ssh()
    try:
        ctx = _FakeCtx(_FakeUI())
        asyncio.run(api.commands["executor"]("remote wss://gpu-01:8080", ctx))
        assert manager.calls == []  # 直连端点无供给
        sel = get_backend_selection(None)
        assert sel.backend == "executor" and sel.url == "wss://gpu-01:8080"
    finally:
        _teardown()


def test_restore_ssh_entry_flips_selection_only():
    """ssh:// 条目恢复只翻模式格（含 remote_cwd）——隧道执行期懒供给。"""
    api, _manager, _rc = _setup_ssh()
    try:
        sm = _FakeSessionManager(
            [
                _Entry(
                    {
                        "backend": "executor",
                        "url": "ssh://alice@gpu-01",
                        "remote_cwd": "/home/alice/.nova/agent/executor/workspaces/s1",
                        "remote_shell": "bash",
                    }
                )
            ]
        )
        ctx = _FakeCtx(_FakeUI(), session_manager=sm)
        handler = api.handlers["session_start"][0]
        handler(None, ctx)
        sel = get_backend_selection(None)
        assert sel.backend == "executor"
        assert sel.url == "ssh://alice@gpu-01"
        assert sel.remote_cwd == "/home/alice/.nova/agent/executor/workspaces/s1"
        assert ctx.refresh_calls == 1
    finally:
        _teardown()
