"""真实队列管线的多出口、格式、隔离、轮转、flush 与关停测试。"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import stat
import threading
from dataclasses import replace
from pathlib import Path
from typing import Optional
from unittest.mock import ANY

import pytest
from nova_logging import (
    LoggingSettings,
    LoggingStats,
    LogStream,
    SinkSettings,
    configure_logging,
    log_context,
)


def _json_lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def _stream_runtime(stream: LogStream, settings: Optional[LoggingSettings] = None):
    settings = settings or LoggingSettings()
    first_sink = replace(settings.sinks[0], stream=stream)
    return configure_logging(replace(settings, sinks=(first_sink, *settings.sinks[1:])))


def test_stdlib_record_uses_whitelist_and_close_restores_root():
    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    stream = io.StringIO()
    runtime = _stream_runtime(stream)
    logger = logging.getLogger("tests.logging.whitelist")
    try:
        with log_context(requestId="req-1"):
            logger.info(
                "gateway.request.completed",
                extra={"status": 200, "durationMs": 12.5, "body": "secret"},
            )
    finally:
        runtime.close()
        runtime.close()
        runtime.flush()

    assert root.handlers == previous_handlers
    assert root.level == previous_level
    assert _json_lines(stream) == [
        {
            "ts": ANY,
            "level": "info",
            "event": "gateway.request.completed",
            "logger": "tests.logging.whitelist",
            "requestId": "req-1",
            "status": 200,
            "durationMs": 12.5,
        }
    ]
    assert "secret" not in stream.getvalue()


def test_exception_and_redactor_failure_are_fail_closed():
    stream = io.StringIO()
    secret = "Bearer upstream-secret-token"

    def redactor(text: str) -> str:
        if text == "gateway.request.failed":
            raise RuntimeError("redactor failed")
        return text.replace(secret, "[REDACTED_SECRET]")

    runtime = _stream_runtime(stream, LoggingSettings(redactor=redactor))
    try:
        try:
            raise RuntimeError(f"upstream rejected {secret}")
        except RuntimeError:
            logging.getLogger("tests.logging.exception").error(
                "gateway.request.failed", exc_info=True
            )
    finally:
        runtime.close()

    record = _json_lines(stream)[0]
    assert record["event"] == "[REDACTED]"
    assert secret not in stream.getvalue()
    assert "[REDACTED_SECRET]" in record["exception"]
    assert "RuntimeError" in record["exception"]


def test_console_output_is_human_readable_without_ansi():
    stream = io.StringIO()
    runtime = _stream_runtime(
        stream, LoggingSettings(sinks=(SinkSettings(json_output=False),))
    )
    try:
        with log_context(requestId="req-console"):
            logging.getLogger("tests.logging.console").info("gateway.stream.closed")
    finally:
        runtime.close()

    output = stream.getvalue()
    assert "gateway.stream.closed" in output
    assert "requestId=req-console" in output
    assert "\x1b[" not in output


@pytest.mark.asyncio
async def test_async_contexts_do_not_leak_between_tasks():
    stream = io.StringIO()
    runtime = _stream_runtime(stream)
    barrier = asyncio.Barrier(2)
    logger = logging.getLogger("tests.logging.async_context")

    async def emit(request_id: str) -> None:
        with log_context(requestId=request_id):
            await barrier.wait()
            logger.info("gateway.request.completed")

    try:
        await asyncio.gather(emit("req-a"), emit("req-b"))
    finally:
        runtime.close()

    records = _json_lines(stream)
    assert {record["requestId"] for record in records} == {"req-a", "req-b"}
    assert len(records) == 2


def test_sinks_have_independent_formats_levels_and_longest_prefix_filters():
    console = io.StringIO()
    structured = io.StringIO()
    runtime = configure_logging(
        LoggingSettings(
            sinks=(
                SinkSettings(stream=console, json_output=False),
                SinkSettings(
                    stream=structured,
                    level=logging.INFO,
                    target_levels={
                        "tests": logging.WARNING,
                        "tests.noisy": logging.DEBUG,
                    },
                ),
            )
        )
    )
    try:
        logging.getLogger("tests.multi").info("tests.info")
        logging.getLogger("tests.multi").warning("tests.warning")
        logging.getLogger("tests.noisy").debug("tests.debug")
    finally:
        runtime.close()

    console_output = console.getvalue()
    assert "tests.info" in console_output
    assert "tests.warning" in console_output
    assert "tests.debug" not in console_output
    records = _json_lines(structured)
    assert [record["event"] for record in records] == ["tests.warning", "tests.debug"]


def test_file_sink_rotates_on_all_platforms(tmp_path: Path):
    path = tmp_path / "logs" / "nova.jsonl"
    runtime = configure_logging(
        LoggingSettings(
            sinks=(SinkSettings(file_path=path, max_file_bytes=300, backup_count=1),)
        )
    )
    try:
        with log_context(requestId="x" * 180):
            for _ in range(8):
                logging.getLogger("tests.logging.rotation").info(
                    "gateway.request.completed"
                )
    finally:
        runtime.close()

    assert path.exists()
    assert path.with_suffix(".jsonl.1").exists()
    assert path.read_text("utf-8").count("gateway.request.completed") >= 1


@pytest.mark.skipif(os.name != "posix", reason="POSIX 文件权限断言")
def test_file_sink_forces_private_permissions(tmp_path: Path):
    path = tmp_path / "logs" / "nova.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("old\n", encoding="utf-8")
    path.chmod(0o644)
    runtime = configure_logging(
        LoggingSettings(
            sinks=(SinkSettings(file_path=path, max_file_bytes=300, backup_count=1),)
        )
    )
    try:
        with log_context(requestId="x" * 180):
            for _ in range(8):
                logging.getLogger("tests.logging.permissions").info(
                    "gateway.request.completed"
                )
    finally:
        runtime.close()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.with_suffix(".jsonl.1").stat().st_mode) == 0o600


class _BlockingStream(io.TextIOBase):
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.writes: list[str] = []

    def write(self, text: str) -> int:
        self.started.set()
        self.release.wait(timeout=5)
        self.writes.append(text)
        return len(text)

    def flush(self) -> None:
        pass


def test_full_queue_drops_new_records_without_blocking_business_thread():
    stream = _BlockingStream()
    runtime = _stream_runtime(stream, LoggingSettings(queue_capacity=1))
    logger = logging.getLogger("tests.logging.backpressure")
    finished = threading.Event()
    emitter: Optional[threading.Thread] = None

    def emit_many() -> None:
        for _ in range(20):
            logger.info("gateway.request.completed")
        finished.set()

    try:
        logger.info("gateway.request.started")
        assert stream.started.wait(timeout=1)
        emitter = threading.Thread(target=emit_many)
        emitter.start()
        assert finished.wait(timeout=1), "QueueHandler 阻塞了业务线程"
    finally:
        stream.release.set()
        runtime.close()
        if emitter is not None:
            emitter.join(timeout=1)

    assert len(stream.writes) < 21
    assert runtime.stats().dropped_records >= 1


class _FailingStream(io.TextIOBase):
    def write(self, text: str) -> int:
        raise OSError("disk unavailable")

    def flush(self) -> None:
        pass


def test_sink_failure_does_not_escape_into_business_or_shutdown(
    capsys: pytest.CaptureFixture[str],
):
    runtime = _stream_runtime(_FailingStream())
    try:
        logging.getLogger("tests.logging.sink_failure").error(
            "gateway.request.failed: Bearer sink-secret-token"
        )
        runtime.flush(timeout=1)
    finally:
        runtime.close()

    stderr = capsys.readouterr().err
    assert "OSError" in stderr
    assert "sink-secret-token" not in stderr
    assert runtime.stats() == LoggingStats(dropped_records=0, sink_failures=1)


def test_flush_waits_until_preceding_records_leave_the_sink():
    stream = _BlockingStream()
    runtime = _stream_runtime(stream)
    flushed = threading.Event()

    def flush() -> None:
        runtime.flush()
        flushed.set()

    try:
        logging.getLogger("tests.logging.flush").info("gateway.request.completed")
        assert stream.started.wait(timeout=1)
        worker = threading.Thread(target=flush)
        worker.start()
        assert not flushed.wait(timeout=0.05)
        stream.release.set()
        assert flushed.wait(timeout=1)
        worker.join(timeout=1)
    finally:
        stream.release.set()
        runtime.close()


def test_flush_timeout_does_not_cancel_the_barrier():
    stream = _BlockingStream()
    runtime = _stream_runtime(stream)
    try:
        logging.getLogger("tests.logging.flush_timeout").info(
            "gateway.request.completed"
        )
        assert stream.started.wait(timeout=1)
        with pytest.raises(TimeoutError, match="draining"):
            runtime.flush(timeout=0)
    finally:
        stream.release.set()
        runtime.close()

    assert len(stream.writes) == 1


def test_close_timeout_keeps_draining_and_can_be_joined_again():
    stream = _BlockingStream()
    runtime = _stream_runtime(stream)
    logging.getLogger("tests.logging.close_timeout").info("gateway.request.completed")
    assert stream.started.wait(timeout=1)

    with pytest.raises(TimeoutError, match="time budget"):
        runtime.close(timeout=0)
    stream.release.set()
    runtime.close(timeout=1)
    runtime.close(timeout=0)

    assert len(stream.writes) == 1


def test_full_queue_flush_reports_enqueue_timeout():
    stream = _BlockingStream()
    runtime = _stream_runtime(stream, LoggingSettings(queue_capacity=1))
    logger = logging.getLogger("tests.logging.full_queue_flush")
    try:
        logger.info("gateway.request.started")
        assert stream.started.wait(timeout=1)
        logger.info("gateway.request.completed")
        with pytest.raises(TimeoutError, match="entering"):
            runtime.flush(timeout=0)
    finally:
        stream.release.set()
        runtime.close()


def test_second_active_runtime_is_rejected_until_first_close():
    root = logging.getLogger()
    previous_handlers = list(root.handlers)
    previous_level = root.level
    first = _stream_runtime(io.StringIO())
    try:
        with pytest.raises(RuntimeError, match="already configured"):
            _stream_runtime(io.StringIO())
    finally:
        first.close()
    second = _stream_runtime(io.StringIO())
    second.close()

    assert root.handlers == previous_handlers
    assert root.level == previous_level


def test_sink_configuration_rejects_invalid_combinations(tmp_path: Path):
    with pytest.raises(ValueError, match="At least one"):
        LoggingSettings(sinks=())
    with pytest.raises(ValueError, match="mutually exclusive"):
        SinkSettings(stream=io.StringIO(), file_path=tmp_path / "nova.jsonl")
