"""executor 接入模块测试（runtime 模式格 + 后端解析 + ExecutorBashOperations）。

真实本地 executor 集成测试归 integration（需 nova-executor 二进制）。
"""

import asyncio

import pytest
from nova_coding_agent.executor import (
    BackendSelection,
    ExecutorBashOperations,
    SpawnPolicy,
    get_backend_selection,
    reset_backend_selection,
    set_backend_selection,
)


@pytest.fixture(autouse=True)
def _clean_selection():
    reset_backend_selection()
    yield
    reset_backend_selection()


class TestBackendSelection:
    """runtime 模式格：默认解析 / 显式翻转 / 重置。

    默认执行姿态归 exec-server 配置根（sandbox_mode 物化）——测试经
    monkeypatch 隔离，不读真实 ``~/.nova/exec-server/config.toml``。
    """

    @staticmethod
    def _patch_config(monkeypatch, tmp_path, mode: str | None):
        home = tmp_path / "home"
        home.mkdir()
        text = f'sandbox_mode = "{mode}"\n' if mode else ""
        (home / "config.toml").write_text(text)
        import nova_coding_agent.executor.runtime as runtime_mod
        from nova_exec_server_client import load_executor_config

        monkeypatch.setattr(
            runtime_mod,
            "load_executor_config",
            lambda **_: load_executor_config(executor_home=home),
        )

    def test_default_local_without_sandbox_mode(self, monkeypatch, tmp_path):
        self._patch_config(monkeypatch, tmp_path, None)
        assert get_backend_selection().backend == "local"

    def test_default_executor_loopback_from_sandbox_mode(self, monkeypatch, tmp_path):
        """config.toml 配了 sandbox_mode → 默认本地回环 executor + 物化策略。"""
        self._patch_config(monkeypatch, tmp_path, "read-only")
        sel = get_backend_selection()
        assert sel.backend == "executor"
        assert sel.url is None
        assert sel.spawn_policy is not None

    def test_explicit_switch_wins_over_default(self, monkeypatch, tmp_path):
        self._patch_config(monkeypatch, tmp_path, None)
        set_backend_selection(BackendSelection(backend="executor", url="ws://x:1"))
        sel = get_backend_selection()
        assert sel.backend == "executor"
        assert sel.url == "ws://x:1"


class TestBashToolBackendResolution:
    """bash 工具执行期解析：local 默认 / executor 按选择构造与复用。"""

    def _make_tool(self):
        import importlib.util
        import os

        from nova_harness.types.resources.tools import (
            NULL_TOOL_SETTINGS,
            ToolContext,
        )

        tool_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "tools", "bash.py"
        )
        spec = importlib.util.spec_from_file_location("_test_bash_tool", tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        context = ToolContext(cwd=os.getcwd(), settings=NULL_TOOL_SETTINGS)
        return module.Tool(context)

    def test_default_resolves_local(self):
        tool = self._make_tool()
        assert tool._resolve_operations() is tool._local_operations

    def test_executor_mode_constructs_and_reuses(self):
        tool = self._make_tool()
        set_backend_selection(BackendSelection(backend="executor"))
        ops1 = tool._resolve_operations()
        assert isinstance(ops1, ExecutorBashOperations)
        assert tool._resolve_operations() is ops1  # 同 url 复用

    def test_executor_url_change_rebuilds(self):
        tool = self._make_tool()
        set_backend_selection(BackendSelection(backend="executor", url="ws://a:1"))
        ops1 = tool._resolve_operations()
        set_backend_selection(BackendSelection(backend="executor", url="ws://b:2"))
        ops2 = tool._resolve_operations()
        assert ops2 is not ops1  # url 变化 → 重建

    def test_executor_remote_cwd_change_rebuilds(self):
        tool = self._make_tool()
        set_backend_selection(
            BackendSelection(
                backend="executor", url="ssh://a@h", remote_cwd="/data/one"
            )
        )
        ops1 = tool._resolve_operations()
        set_backend_selection(
            BackendSelection(
                backend="executor", url="ssh://a@h", remote_cwd="/data/two"
            )
        )
        ops2 = tool._resolve_operations()
        assert ops2 is not ops1  # remote_cwd 变化 → 重建
        assert ops2._remote_cwd == "/data/two"

    def test_spawn_policy_override_forces_desandbox(self):
        """升级重试姿态：显式 spawn_policy_override=None 强制脱沙箱——
        该分支经 BackendSelection 重构 selection（回归：bash.py 曾漏导入
        BackendSelection，此分支一触即 NameError——0f2f50e3 引入、
        默认路径测试从未触达）。"""
        tool = self._make_tool()
        set_backend_selection(
            BackendSelection(
                backend="executor",
                url="ws://a:1",
                spawn_policy=SpawnPolicy(sandbox={"cwd": "/tmp", "permissions": {}}),
            )
        )
        ops = tool._resolve_operations(spawn_policy_override=None)
        assert isinstance(ops, ExecutorBashOperations)
        assert ops._policy is None  # 脱沙箱姿态生效（策略被显式清空）

    def test_switch_back_to_local(self):
        tool = self._make_tool()
        set_backend_selection(BackendSelection(backend="executor"))
        assert isinstance(tool._resolve_operations(), ExecutorBashOperations)
        set_backend_selection(BackendSelection(backend="local"))
        assert tool._resolve_operations() is tool._local_operations


# ---------------------------------------------------------------------------
# ExecutorBashOperations：remote_cwd 执行 cwd 覆盖
# ---------------------------------------------------------------------------


class _FakeProcHandle:
    def __init__(self):
        self.start_params = None

    def output(self):
        class _Stream:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        return _Stream()

    async def read(self, wait_ms=0):
        class _Out:
            exit_code = 0

        return _Out()

    async def terminate(self):
        pass


class _FakeClient:
    def __init__(self, handle):
        self._handle = handle
        self.process = type("P", (), {"start": self._start})()

    async def _start(self, **params):
        self._handle.start_params = params
        return self._handle

    async def environment_info(self):
        raise RuntimeError("no env info")  # shell 回落 bash


class _FakeManagerForOps:
    def __init__(self, client):
        self._client = client

    async def get_client(self, url=None):
        return self._client


class TestExecutorBashOperationsRemoteCwd:
    def _run(self, ops, cwd):
        return asyncio.run(ops.execute("echo hi", cwd, {}))

    def test_remote_cwd_overrides_local_cwd(self):
        handle = _FakeProcHandle()
        ops = ExecutorBashOperations(
            _FakeManagerForOps(_FakeClient(handle)),
            url="ssh://alice@gpu-01",
            remote_cwd="/home/alice/.nova/agent/executor/workspaces/s1",
        )
        result = self._run(ops, "/Users/local/project")
        assert result.exit_code == 0
        # 远程后端：执行 cwd 必须是远程路径，不是本地 cwd
        assert handle.start_params["cwd"] == (
            "file:///home/alice/.nova/agent/executor/workspaces/s1"
        )

    def test_no_remote_cwd_uses_caller_cwd(self):
        handle = _FakeProcHandle()
        ops = ExecutorBashOperations(_FakeManagerForOps(_FakeClient(handle)), url=None)
        self._run(ops, "/Users/local/project")
        assert handle.start_params["cwd"] == "file:///Users/local/project"
