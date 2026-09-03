"""OutputGuard 单元测试。"""

import io
import logging

from nova_harness.core.utils.output_guard import OutputGuard


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
