"""
nova-harness-rpc CLI 单元测试。
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova_harness.modes.rpc.cli import _async_main, _build_parser, main


def test_build_parser_version():
    """--version 应正常输出并退出。"""
    parser = _build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])
    assert exc.value.code == 0


@pytest.mark.asyncio
async def test_async_main_runs_server():
    """_async_main 应创建服务器、注册信号处理器并运行。"""
    mock_server = MagicMock()
    mock_server.run = AsyncMock()
    mock_loop = MagicMock()
    mock_loop.add_signal_handler = MagicMock()
    mock_loop.remove_signal_handler = MagicMock()

    with patch.object(sys, "argv", ["nova-harness-rpc"]):
        with patch(
            "nova_harness.modes.rpc.cli.NovaRpcServer", return_value=mock_server
        ):
            with patch("asyncio.get_running_loop", return_value=mock_loop):
                result = await _async_main()

    assert result == 0
    mock_server.run.assert_awaited_once()
    assert mock_loop.add_signal_handler.call_count == 2
    assert mock_loop.remove_signal_handler.call_count == 2


@pytest.mark.asyncio
async def test_async_main_calls_shutdown_on_signal():
    """信号处理器被触发时应调用 server.shutdown()。"""
    mock_server = MagicMock()
    mock_server.run = AsyncMock()
    mock_loop = MagicMock()
    handlers = {}

    def capture(sig, cb, *args):
        handlers[sig] = (cb, args)

    mock_loop.add_signal_handler.side_effect = capture

    with patch.object(sys, "argv", ["nova-harness-rpc"]):
        with patch(
            "nova_harness.modes.rpc.cli.NovaRpcServer", return_value=mock_server
        ):
            with patch("asyncio.get_running_loop", return_value=mock_loop):
                await _async_main()

    for sig, (cb, args) in handlers.items():
        cb(*args)
    assert mock_server.shutdown.call_count == 2


def test_main_returns_keyboard_interrupt_code():
    """用户按 Ctrl-C 时返回 130。"""

    async def raise_interrupt():
        raise KeyboardInterrupt

    with patch("nova_harness.modes.rpc.cli._async_main", raise_interrupt):
        assert main() == 130


def test_main_propagates_return_code():
    """正常结束时返回 _async_main 的返回值。"""

    async def fake_async_main():
        return 0

    with patch("nova_harness.modes.rpc.cli._async_main", fake_async_main):
        assert main() == 0
