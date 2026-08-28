"""ExecutorBashOperations 真实 executor 集成测试（需 nova-executor 二进制）。

标记 integration：默认不跑（无二进制/离线环境跳过）。
"""

import asyncio

import pytest
from nova_coding_agent.executor import (
    ExecutorBashOperations,
    get_executor_manager,
    resolve_executor_binary,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def executor_available():
    if resolve_executor_binary() is None:
        pytest.skip("nova-executor 二进制不可用")
    return True


class TestExecutorBashOperationsIntegration:
    def test_execute_echo(self, executor_available):
        async def run():
            ops = ExecutorBashOperations(get_executor_manager())
            try:
                result = await ops.execute("echo nova-it-ok", "/tmp", {})
                assert result.exit_code == 0
                assert "nova-it-ok" in result.output
            finally:
                await get_executor_manager().close_all()

        asyncio.run(run())

    def test_execute_nonzero_exit(self, executor_available):
        async def run():
            ops = ExecutorBashOperations(get_executor_manager())
            try:
                result = await ops.execute("exit 7", "/tmp", {})
                assert result.exit_code == 7
            finally:
                await get_executor_manager().close_all()

        asyncio.run(run())

    def test_abort(self, executor_available):
        async def run():
            from nova_ai import AbortController

            ctl = AbortController()
            ops = ExecutorBashOperations(get_executor_manager())

            async def abort_soon():
                await asyncio.sleep(0.3)
                ctl.abort()

            asyncio.create_task(abort_soon())
            try:
                result = await ops.execute("sleep 30", "/tmp", {"signal": ctl.signal})
                assert result.cancelled is True
                assert result.exit_code is None
            finally:
                await get_executor_manager().close_all()

        asyncio.run(run())
