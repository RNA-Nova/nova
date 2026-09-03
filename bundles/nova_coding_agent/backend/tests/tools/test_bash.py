"""bash tool executor 单元测试（对齐 TS 行为）。"""

import asyncio
import os
import tempfile

import pytest


def _load_executor(tool_name: str = "bash", settings=None):
    import importlib.util

    executor_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "tools", f"{tool_name}.py"
    )
    spec = importlib.util.spec_from_file_location(
        f"_test_tool_{tool_name}", executor_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from nova_harness.core.types.resources.tools import (
        NULL_TOOL_SETTINGS,
        ToolContext,
    )

    context = ToolContext(cwd=os.getcwd(), settings=settings or NULL_TOOL_SETTINGS)
    return module.Tool(context)


class _StubSettings:
    """ToolSettingsView 测试桩。"""

    def __init__(self, shell_path=None, shell_command_prefix=None):
        self._shell_path = shell_path
        self._shell_command_prefix = shell_command_prefix

    def get_shell_path(self):
        return self._shell_path

    def get_shell_command_prefix(self):
        return self._shell_command_prefix

    def get_image_auto_resize(self):
        return True


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_bash_echo(tmpdir):
    executor = _load_executor()
    result = _run(executor.execute("id", {"command": "echo hello", "cwd": tmpdir}))

    assert result.details["exit_code"] == 0
    assert "hello" in result.content[0].text
    assert result.details.get("truncated") is False


def test_bash_stderr(tmpdir):
    executor = _load_executor()
    result = _run(executor.execute("id", {"command": "echo err >&2", "cwd": tmpdir}))

    assert result.details["exit_code"] == 0
    assert "err" in result.content[0].text


def test_bash_non_zero_exit(tmpdir):
    executor = _load_executor()
    result = _run(executor.execute("id", {"command": "exit 42", "cwd": tmpdir}))

    assert result.details["exit_code"] == 42
    assert "❌" in result.content[0].text


def test_bash_timeout(tmpdir):
    executor = _load_executor()
    result = _run(
        executor.execute("id", {"command": "sleep 10", "cwd": tmpdir, "timeout": 0.1})
    )

    assert "超时" in result.content[0].text


def test_bash_cancel(tmpdir):
    from nova_ai import AbortController

    executor = _load_executor()
    controller = AbortController()

    async def run_and_cancel():
        task = asyncio.create_task(
            executor.execute(
                "id", {"command": "sleep 10", "cwd": tmpdir}, signal=controller.signal
            )
        )
        await asyncio.sleep(0.1)
        controller.abort()
        return await task

    result = _run(run_and_cancel())
    assert "取消" in result.content[0].text


def test_bash_truncation_creates_temp_file(tmpdir):
    executor = _load_executor()
    # 生成超过 50KB 的输出
    result = _run(
        executor.execute(
            "id",
            {"command": "python3 -c \"print('A' * 100000)\"", "cwd": tmpdir},
        )
    )

    assert result.details.get("truncated") is True
    assert result.details.get("full_output_path")
    assert os.path.exists(result.details["full_output_path"])


def test_bash_on_update_streaming(tmpdir):
    executor = _load_executor()
    updates = []

    async def on_update(result):
        updates.append(result)

    _run(
        executor.execute(
            "id",
            {"command": "echo line1 && echo line2", "cwd": tmpdir},
            on_update=on_update,
        )
    )

    # 首次更新是初始空 update（对齐 TS：命令产出前先渲染工具卡片）
    assert len(updates) >= 2
    assert updates[0].content == []
    # 最终更新包含完整输出
    assert "line1" in updates[-1].content[0].text
    assert "line2" in updates[-1].content[0].text


def test_bash_missing_cwd():
    executor = _load_executor()
    result = _run(
        executor.execute("id", {"command": "echo hi", "cwd": "/nonexistent/path"})
    )

    assert "工作目录不存在" in result.content[0].text


def test_bash_missing_command():
    executor = _load_executor()
    result = _run(executor.execute("id", {}))

    assert "必须提供 command 参数" in result.content[0].text


def test_bash_shell_command_prefix_from_settings(tmpdir):
    """settings.shell_command_prefix 在构造期读取并拼进每条命令（对齐 pi commandPrefix）。"""
    executor = _load_executor(
        settings=_StubSettings(shell_command_prefix="export NOVA_MARKER=works")
    )
    result = _run(
        executor.execute("id", {"command": "echo $NOVA_MARKER", "cwd": tmpdir})
    )
    assert result.details["exit_code"] == 0
    assert "works" in result.content[0].text


def test_bash_shell_path_from_settings(tmpdir):
    """settings.shell_path 在构造期传入执行后端。"""
    executor = _load_executor(settings=_StubSettings(shell_path="/bin/bash"))
    assert executor._local_operations.shell_path == "/bin/bash"
    result = _run(executor.execute("id", {"command": "echo ok", "cwd": tmpdir}))
    assert result.details["exit_code"] == 0


def test_bash_timeout_must_be_finite(tmpdir):
    """timeout 非有限值（inf/nan）显式报错（对齐 pi resolveTimeoutMs）。"""
    executor = _load_executor()
    for bad in (float("inf"), float("nan")):
        result = _run(
            executor.execute(
                "id", {"command": "echo hi", "cwd": tmpdir, "timeout": bad}
            )
        )
        assert result.is_error is True
        assert (
            "Invalid timeout: must be a finite number of seconds"
            in result.content[0].text
        )


def test_bash_timeout_must_be_positive(tmpdir):
    """timeout ≤ 0 显式报错（对齐 pi resolveTimeoutMs）。"""
    executor = _load_executor()
    for bad in (0, -5):
        result = _run(
            executor.execute(
                "id", {"command": "echo hi", "cwd": tmpdir, "timeout": bad}
            )
        )
        assert result.is_error is True
        assert (
            "Invalid timeout: must be a finite number of seconds"
            in result.content[0].text
        )


def test_bash_timeout_exceeds_max(tmpdir):
    """timeout 超过上限（2147483.647 秒）显式报错（对齐 pi resolveTimeoutMs）。"""
    executor = _load_executor()
    result = _run(
        executor.execute(
            "id", {"command": "echo hi", "cwd": tmpdir, "timeout": 3_000_000}
        )
    )
    assert result.is_error is True
    assert "Invalid timeout: maximum is" in result.content[0].text


def test_bash_no_default_timeout(tmpdir):
    """不传 timeout 即不限时（对齐 pi：无默认超时，避免误杀长构建命令）。"""
    executor = _load_executor()
    # schema 不再声明默认值
    assert "default" not in executor.parameters["properties"]["timeout"]
    # 不传 timeout 的命令正常执行
    result = _run(executor.execute("id", {"command": "echo ok", "cwd": tmpdir}))
    assert result.is_error is False
    assert result.details["exit_code"] == 0


def test_bash_pre_spawn_aborted_signal(tmpdir):
    """spawn 前 signal 已中止：直接 is_error 返回，不启动进程（对齐 pi）。"""
    from nova_ai import AbortController

    executor = _load_executor()
    controller = AbortController()
    controller.abort()
    marker = os.path.join(tmpdir, "should_not_exist")
    result = _run(
        executor.execute(
            "id",
            {"command": f"touch {marker}", "cwd": tmpdir},
            signal=controller.signal,
        )
    )
    assert result.is_error is True
    assert "取消" in result.content[0].text
    # 进程未启动：副作用文件不存在
    assert not os.path.exists(marker)


def test_bash_description_documents_truncation():
    """description/prompt_snippet 明确截断与落盘语义（对齐 pi bash.ts）。"""
    executor = _load_executor()
    assert "2000" in executor.description
    assert "50KB" in executor.description
    assert "临时文件" in executor.description
    assert executor.prompt_snippet


def test_bash_env_param(tmpdir):
    """env 参数合并进子进程环境（nova 增量参数，pi 由 spawnHook 覆盖此场景）。"""
    executor = _load_executor()
    result = _run(
        executor.execute(
            "id",
            {
                "command": "echo $NOVA_TEST_ENV_MARKER",
                "cwd": tmpdir,
                "env": {"NOVA_TEST_ENV_MARKER": "hello_env"},
            },
        )
    )
    assert result.details["exit_code"] == 0
    assert "hello_env" in result.content[0].text


def test_bash_spawn_hook_rewrites_command(tmpdir):
    """spawn_hook 可在启动前改写 command/cwd/env（对齐 pi spawnHook）。"""
    from nova_harness.core.types.extensions.process import SpawnContext

    executor = _load_executor()

    def hook(ctx: SpawnContext) -> SpawnContext:
        return SpawnContext(command="echo hooked_by_spawn", cwd=ctx.cwd, env=ctx.env)

    result = _run(
        executor.execute(
            "id",
            {"command": "echo original", "cwd": tmpdir, "spawn_hook": hook},
        )
    )
    assert result.details["exit_code"] == 0
    assert "hooked_by_spawn" in result.content[0].text
    assert "original" not in result.details["stdout"]


def test_bash_timeout_keeps_partial_output(tmpdir):
    """超时结果保留已产出的部分输出并透出秒数（对齐 pi appendStatus）。"""
    executor = _load_executor()
    result = _run(
        executor.execute(
            "id",
            {
                "command": "echo before_sleep; sleep 10",
                "cwd": tmpdir,
                "timeout": 0.3,
            },
        )
    )
    assert result.is_error is True
    text = result.content[0].text
    assert "before_sleep" in text
    assert "0.3" in text and "超时" in text
    assert result.details["exit_code"] == -1
    # duration_ms 与成功分支同为 int 毫秒
    assert isinstance(result.details["duration_ms"], int)


def test_bash_cancel_keeps_partial_output(tmpdir):
    """取消结果保留已产出的部分输出（对齐 pi：aborted 时输出 + 状态行）。"""
    from nova_ai import AbortController

    executor = _load_executor()
    controller = AbortController()

    async def run_and_cancel():
        task = asyncio.create_task(
            executor.execute(
                "id",
                {"command": "echo partial_output; sleep 10", "cwd": tmpdir},
                signal=controller.signal,
            )
        )
        await asyncio.sleep(0.3)
        controller.abort()
        return await task

    result = _run(run_and_cancel())
    assert result.is_error is True
    assert "partial_output" in result.content[0].text
    assert "取消" in result.content[0].text


def test_bash_truncation_footer_lines_branch(tmpdir):
    """行数超限的截断 footer 文案（对齐 pi formatOutput 的 lines 分支）。"""
    executor = _load_executor()
    result = _run(
        executor.execute(
            "id",
            {"command": "seq 1 3000", "cwd": tmpdir},
        )
    )
    assert result.details["truncated"] is True
    truncation = result.details["truncation"]
    assert truncation["truncated_by"] == "lines"
    assert truncation["total_lines"] == 3000
    text = result.content[0].text
    assert "[Showing lines" in text
    assert "of 3000." in text
    assert f"Full output: {result.details['full_output_path']}" in text


def test_bash_truncation_footer_bytes_branch(tmpdir):
    """字节超限（多行）的截断 footer 文案（对齐 pi formatOutput 的 bytes 分支）。"""
    executor = _load_executor()
    # 100 行 × 2KB ≈ 200KB > 50KB 上限；行数远低于 2000，触发 bytes 分支
    result = _run(
        executor.execute(
            "id",
            {
                "command": (
                    'python3 -c "'
                    "line = 'x' * 2048\n"
                    'for _ in range(100): print(line)"'
                ),
                "cwd": tmpdir,
            },
        )
    )
    assert result.details["truncated"] is True
    assert result.details["truncation"]["truncated_by"] == "bytes"
    text = result.content[0].text
    assert "KB limit" in text
    assert f"Full output: {result.details['full_output_path']}" in text


def test_bash_truncation_footer_last_line_partial(tmpdir):
    """单行超长的截断 footer 文案（对齐 pi formatOutput 的 lastLinePartial 分支）。"""
    executor = _load_executor()
    result = _run(
        executor.execute(
            "id",
            {"command": "python3 -c \"print('A' * 100000)\"", "cwd": tmpdir},
        )
    )
    assert result.details["truncated"] is True
    assert result.details["truncation"]["last_line_partial"] is True
    text = result.content[0].text
    assert "[Showing last" in text
    assert "of line 1 (line is" in text


def test_tool_metadata_valid():
    """Tool 类必须声明完整元数据（类属性）。"""
    executor = _load_executor()
    assert executor.name == "bash"
    assert isinstance(executor.description, str) and executor.description
    assert isinstance(executor.parameters, dict)
    assert executor.parameters.get("type") == "object"
