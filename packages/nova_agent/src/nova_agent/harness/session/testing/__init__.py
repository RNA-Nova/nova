"""Session 后端一致性测试基建（对齐 TS ``harness/session/testing/``）。"""

from .conformance import (
    ConformanceCase,
    FixtureFactory,
    create_session_backend_conformance,
)

__all__ = [
    "ConformanceCase",
    "FixtureFactory",
    "create_session_backend_conformance",
]
