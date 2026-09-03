"""SDK 包管理进度回调桥接测试。"""

from nova_harness.core.sdk import _resolve_on_progress
from nova_harness.core.types.package import ProgressEvent
from nova_harness.core.types.session.config import CreateAgentSessionOptions


class _RecordingUIContext:
    def __init__(self) -> None:
        self.calls = []

    def notify(self, method, params) -> None:
        self.calls.append((method, params))


def test_on_progress_explicit_wins() -> None:
    """显式 on_progress 优先于 ui_context 桥接。"""
    events = []
    callback = events.append
    options = CreateAgentSessionOptions(
        on_progress=callback,
        ui_context=_RecordingUIContext(),
    )
    assert _resolve_on_progress(options) is callback


def test_on_progress_bridges_to_ui_context() -> None:
    """未显式给定时从 ui_context 桥接为 package_progress 通知。"""
    ui = _RecordingUIContext()
    options = CreateAgentSessionOptions(ui_context=ui)

    callback = _resolve_on_progress(options)
    assert callback is not None

    event = ProgressEvent(
        type="start",
        action="install",
        source="pkg-x",
        message="Installing...",
        percent=0.3,
    )
    callback(event)

    assert ui.calls == [
        (
            "package_progress",
            {
                "type": "start",
                "action": "install",
                "source": "pkg-x",
                "message": "Installing...",
                "percent": 0.3,
            },
        )
    ]


def test_on_progress_none_without_ui_context() -> None:
    """既无显式回调也无 ui_context 时返回 None。"""
    assert _resolve_on_progress(CreateAgentSessionOptions()) is None
