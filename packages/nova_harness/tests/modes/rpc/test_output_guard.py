"""OutputGuard 单元测试。"""

import io
import logging

from nova_harness.core.utils.output_guard import (
    OutputGuard,
    is_stdout_taken_over,
    restore_stdout,
    take_over_stdout,
)


def test_output_guard_redirects_non_protocol_writes_to_stderr():
    """非协议写入应被重定向到 stderr。"""
    stdout = io.StringIO()
    stderr = io.StringIO()

    with OutputGuard(stdout=stdout, stderr=stderr):
        print("should go to stderr", file=stdout)

    assert stdout.getvalue() == ""
    assert "should go to stderr" in stderr.getvalue()


def test_output_guard_allows_protocol_writes_to_stdout():
    """标记为 protocol_write 的写入可以正常进入 stdout。"""
    stdout = io.StringIO()
    stderr = io.StringIO()

    with OutputGuard(stdout=stdout, stderr=stderr) as guard:
        with guard.protocol_write():
            stdout.write("protocol line\n")

    assert "protocol line\n" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_output_guard_restores_stdout_on_exit():
    """退出后 stdout.write 恢复原始行为。"""
    stdout = io.StringIO()
    stderr = io.StringIO()

    with OutputGuard(stdout=stdout, stderr=stderr):
        pass

    original_write = stdout.write
    stdout.write("after guard")
    assert "after guard" in stdout.getvalue()
    assert stdout.write is original_write


def test_output_guard_redirects_logging_handlers_to_stderr():
    """目标为 stdout 的 logging handler 应被重定向到 stderr。"""
    stdout = io.StringIO()
    stderr = io.StringIO()

    handler = logging.StreamHandler(stdout)
    handler.setLevel(logging.DEBUG)
    logger = logging.getLogger("test_output_guard_logger")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    try:
        with OutputGuard(stdout=stdout, stderr=stderr):
            logger.info("log to stderr")

        assert "log to stderr" in stderr.getvalue()
        assert "log to stderr" not in stdout.getvalue()
    finally:
        logger.removeHandler(handler)


# ---------------------------------------------------------------------------
# 激活状态单一事实源（双 API 断链回归——历史事故：RPC 入口用上下文管理器
# 局部实例，is_stdout_taken_over 只看单例 → 子进程 stdio 决策恒判"未接管"，
# pip 输出泄漏进协议通道。任何 install() 都必须让探针说真话）
# ---------------------------------------------------------------------------


def test_context_manager_install_registers_as_taken_over():
    """with OutputGuard()（上下文管理器路径）同样让 is_stdout_taken_over 为真。"""
    assert not is_stdout_taken_over()
    with OutputGuard(stdout=io.StringIO(), stderr=io.StringIO()):
        assert is_stdout_taken_over()
    assert not is_stdout_taken_over()


def test_singleton_install_registers_as_taken_over():
    """take_over_stdout()（单例路径）同样登记，restore 后解除。"""
    assert not is_stdout_taken_over()
    take_over_stdout(stdout=io.StringIO(), stderr=io.StringIO())
    try:
        assert is_stdout_taken_over()
    finally:
        restore_stdout()
    assert not is_stdout_taken_over()


def test_stdio_kwargs_follow_context_manager_guard():
    """子进程 stdio 决策（_stdio_kwargs）跟随任一安装路径的活动 guard：
    活动时改道 stderr + 关 stdin（不继承协议 fd 1），非活动时直继承。"""
    import subprocess
    import sys as _sys

    from nova_harness.core.package.install.python_backend import _stdio_kwargs

    with OutputGuard(stdout=io.StringIO(), stderr=io.StringIO()):
        kwargs = _stdio_kwargs()
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["stdout"] is _sys.stderr
        assert kwargs["stderr"] is _sys.stderr
    assert _stdio_kwargs() == {}
