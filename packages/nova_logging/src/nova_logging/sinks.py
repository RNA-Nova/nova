"""日志 sink 配置、目标级过滤与受监督的标准出口。"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from dataclasses import dataclass, field
from io import TextIOWrapper
from pathlib import Path
from types import MappingProxyType
from typing import IO, Callable, Mapping, Optional, Protocol, cast

FailureReporter = Callable[[Optional[BaseException]], None]


class LogStream(Protocol):
    """日志流的真实最小输入面；stdlib StreamHandler 只消费这两个方法。"""

    def write(self, text: str, /) -> int: ...

    def flush(self) -> None: ...


@dataclass(frozen=True, kw_only=True)
class SinkSettings:
    """一个独立出口；缺省为 INFO 级 JSON stdout。"""

    level: int = logging.INFO
    target_levels: Mapping[str, int] = field(default_factory=dict)
    json_output: bool = True
    stream: Optional[LogStream] = field(default=None, repr=False, compare=False)
    file_path: Optional[Path] = None
    max_file_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "target_levels", MappingProxyType(dict(self.target_levels))
        )
        if any(not target for target in self.target_levels):
            raise ValueError("target level names must not be empty")
        if self.stream is not None and self.file_path is not None:
            raise ValueError("stream and file_path are mutually exclusive")
        if self.max_file_bytes < 0:
            raise ValueError("max_file_bytes must be non-negative")
        if self.backup_count < 0:
            raise ValueError("backup_count must be non-negative")


class TargetFilter(logging.Filter):
    """按 logger 名最长前缀匹配；未命中时使用 sink 默认级别。"""

    def __init__(self, default_level: int, target_levels: Mapping[str, int]) -> None:
        super().__init__()
        self._default_level = default_level
        self._target_levels = tuple(
            sorted(target_levels.items(), key=lambda item: len(item[0]), reverse=True)
        )

    def filter(self, record: logging.LogRecord) -> bool:
        threshold = self._default_level
        for target, level in self._target_levels:
            if record.name == target or record.name.startswith(f"{target}."):
                threshold = level
                break
        return record.levelno >= threshold


class _SinkErrorMixin:
    """取代 stdlib 默认 handleError，统一进入 runtime 的脱敏失败出口。"""

    _on_failure: FailureReporter

    def handleError(self, record: logging.LogRecord) -> None:
        self._on_failure(sys.exc_info()[1])


class _NovaStreamHandler(_SinkErrorMixin, logging.StreamHandler):
    def __init__(self, stream: LogStream, on_failure: FailureReporter) -> None:
        self._on_failure = on_failure
        # 此处只收窄到 LogStream 已声明的 write/flush 使用面，不补充行为假设。
        super().__init__(cast(IO[str], stream))


class _SecureRotatingFileHandler(_SinkErrorMixin, logging.handlers.RotatingFileHandler):
    """POSIX 上每次新建当前文件都强制 0600；轮转副本继承同一权限。"""

    def __init__(
        self,
        file_path: Path,
        on_failure: FailureReporter,
        *,
        max_file_bytes: int,
        backup_count: int,
    ) -> None:
        self._on_failure = on_failure
        super().__init__(
            file_path,
            maxBytes=max_file_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )

    def _open(self) -> TextIOWrapper:
        if os.name != "posix":
            return super()._open()
        fd = os.open(
            self.baseFilename,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            # 已存在文件的 mode 不会被 os.open 的 mode 收窄，显式校正。
            os.chmod(self.baseFilename, 0o600)
            return cast(
                TextIOWrapper,
                os.fdopen(fd, self.mode, encoding=self.encoding, errors=self.errors),
            )
        except Exception:
            os.close(fd)
            raise


def create_sink(settings: SinkSettings, on_failure: FailureReporter) -> logging.Handler:
    if settings.file_path is None:
        stream = settings.stream if settings.stream is not None else sys.stdout
        return _NovaStreamHandler(stream, on_failure)
    settings.file_path.parent.mkdir(parents=True, exist_ok=True)
    return _SecureRotatingFileHandler(
        settings.file_path,
        on_failure,
        max_file_bytes=settings.max_file_bytes,
        backup_count=settings.backup_count,
    )


__all__ = [
    "FailureReporter",
    "LogStream",
    "SinkSettings",
    "TargetFilter",
    "create_sink",
]
