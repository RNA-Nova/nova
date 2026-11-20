"""可配置的真实 Relay 集成测试。

默认跳过；设置 NOVA_RELAY_LIVE_* 环境后，可用 pytest 或标准库 unittest
在真实控制端运行。目标节点、目录、shell 均由环境指定，测试不绑定部署路径。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import shlex
import unittest
import uuid
from pathlib import Path

from nova_exec_protocol import FileSystemSandboxContext

from nova_exec_server_client import (
    AuthError,
    ExecutorClient,
    FileSystemError,
    ProtocolError,
    TransportError,
)

_REQUIRED_ENV = (
    "NOVA_RELAY_LIVE_TOKEN_PATH",
    "NOVA_RELAY_LIVE_RELAY_CLIENT",
    "NOVA_RELAY_LIVE_ENVIRONMENT_ID",
    "NOVA_RELAY_LIVE_TARGET_DIR",
    "NOVA_RELAY_LIVE_EXPECTED_UNAME",
    "NOVA_RELAY_LIVE_SHELL",
    "NOVA_RELAY_LIVE_CWD",
)


@unittest.skipUnless(
    all(os.environ.get(name) for name in _REQUIRED_ENV),
    "未配置 NOVA_RELAY_LIVE_* 真机测试环境",
)
class TestRelayLive(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.token = json.loads(
            Path(os.environ["NOVA_RELAY_LIVE_TOKEN_PATH"]).read_text()
        )["harness_token"]
        self.target_dir = os.environ["NOVA_RELAY_LIVE_TARGET_DIR"].rstrip("/")
        self.expected_uname = os.environ["NOVA_RELAY_LIVE_EXPECTED_UNAME"]
        self.shell = os.environ["NOVA_RELAY_LIVE_SHELL"]
        self.cwd = os.environ["NOVA_RELAY_LIVE_CWD"]
        self.cleanup_paths: list[str] = []
        self.client = ExecutorClient.from_relay(
            registry_url=os.environ.get(
                "NOVA_RELAY_LIVE_REGISTRY_URL", "https://180.184.33.245"
            ),
            environment_id=os.environ["NOVA_RELAY_LIVE_ENVIRONMENT_ID"],
            token=self.token,
            relay_client=os.environ["NOVA_RELAY_LIVE_RELAY_CLIENT"],
            connections=2,
            stderr_handler=lambda line: print(
                f"relay-bridge: {line.replace(self.token, '<redacted>')}", flush=True
            ),
        )
        await self.client.connect()

    async def asyncTearDown(self):
        for path in reversed(self.cleanup_paths):
            try:
                await asyncio.wait_for(self.client.fs.remove(path), timeout=5)
            except Exception:
                pass
        await asyncio.wait_for(self.client.disconnect(), timeout=15)
        settle = int(os.environ.get("NOVA_RELAY_LIVE_FAULT_SETTLE_SECONDS", "0"))
        if settle > 0 and self._testMethodName.startswith("test_relay_restart"):
            await self.wait_fresh_environment_ready()
            await asyncio.sleep(settle)

    def new_path(self, label: str) -> str:
        path = f"{self.target_dir}/nova-live-{label}-{uuid.uuid4().hex}.bin"
        self.cleanup_paths.append(path)
        return path

    def make_client(self, environment_id: str, token: str) -> ExecutorClient:
        return ExecutorClient.from_relay(
            registry_url=os.environ.get(
                "NOVA_RELAY_LIVE_REGISTRY_URL", "https://180.184.33.245"
            ),
            environment_id=environment_id,
            token=token,
            relay_client=os.environ["NOVA_RELAY_LIVE_RELAY_CLIENT"],
            connections=2,
            stderr_handler=lambda line: print(
                f"relay-bridge: {line.replace(token, '<redacted>')}", flush=True
            ),
        )

    async def run_command(self, argv, cwd=None):
        handle = await self.client.process.start(argv=argv, cwd=cwd or self.cwd)
        chunks = []
        while True:
            response = await handle.read(wait_ms=1000)
            chunks.extend(response.chunks)
            if response.exited:
                output = b"".join(chunks)
                self.assertEqual(response.exit_code, 0, (response, output))
                return output

    async def control_probe(self):
        await asyncio.wait_for(self.client.environment_info(), timeout=15)
        output = await asyncio.wait_for(
            self.run_command(
                [self.shell, "-c", "printf 'relay-live-control-ok\\n'; uname -s"]
            ),
            timeout=30,
        )
        self.assertIn(b"relay-live-control-ok", output)
        self.assertIn(self.expected_uname.encode(), output)

    def require_fault_command(self):
        command = os.environ.get("NOVA_RELAY_LIVE_FAULT_COMMAND")
        if not command:
            self.skipTest("未配置 NOVA_RELAY_LIVE_FAULT_COMMAND 故障命令")
        return command

    async def inject_fault(self):
        command = self.require_fault_command()
        process = await asyncio.create_subprocess_shell(command)
        await asyncio.wait_for(process.wait(), timeout=45)
        self.assertEqual(process.returncode, 0, f"故障命令失败: {command}")

    async def wait_control_ready(self, timeout=30):
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            try:
                # environment_info 可能读缓存；目录 metadata 必定发 RPC，才能真正
                # 证明控制通道已经恢复，而不是仅看到旧的初始化元数据。
                await self.client.fs.metadata(self.target_dir)
                return
            except (TransportError, ConnectionError):
                if asyncio.get_running_loop().time() >= deadline:
                    raise
                await asyncio.sleep(0.25)

    async def wait_fresh_environment_ready(self, timeout=60):
        """故障用例之间用全新连接确认节点已上线，避免上一例退避污染下一例。"""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            probe = self.make_client(
                os.environ["NOVA_RELAY_LIVE_ENVIRONMENT_ID"], self.token
            )
            try:
                await asyncio.wait_for(probe.connect(), timeout=10)
            except (TransportError, ProtocolError, asyncio.TimeoutError):
                try:
                    await asyncio.wait_for(probe.disconnect(), timeout=5)
                except Exception:
                    pass
                if asyncio.get_running_loop().time() >= deadline:
                    raise
                await asyncio.sleep(0.5)
            else:
                await asyncio.wait_for(probe.disconnect(), timeout=15)
                return

    async def read_all(self, path, *, block_size=32 * 1024, sandbox=None):
        chunks = bytearray()
        async for chunk in self.client.fs.read_stream(
            path, block_size=block_size, sandbox=sandbox
        ):
            chunks.extend(chunk)
        return bytes(chunks)

    async def wait_for_size(self, path, expected, timeout=10):
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            metadata = await self.client.fs.metadata(path)
            if metadata.size == expected:
                return
            if asyncio.get_running_loop().time() >= deadline:
                self.fail(f"文件大小未收敛到 {expected}: 当前 {metadata.size}")
            await asyncio.sleep(0.05)

    async def test_stream_sizes_ranges_and_empty_files(self):
        for size in (0, 1, 16 * 1024 + 1, 1024 * 1024 + 1):
            with self.subTest(size=size):
                path = self.new_path(f"size-{size}")
                payload = random.Random(1000 + size).randbytes(size)
                total = await self.client.fs.write_stream(
                    path, [payload], block_size=16 * 1024
                )
                self.assertEqual(total, size)
                self.assertEqual(await self.read_all(path), payload)
                if size > 100:
                    offset = 37
                    length = 4097
                    ranged = bytearray()
                    async for chunk in self.client.fs.read_stream(
                        path,
                        block_size=1024,
                        offset=offset,
                        length=length,
                    ):
                        ranged.extend(chunk)
                    self.assertEqual(bytes(ranged), payload[offset : offset + length])
        await self.control_probe()

    async def test_early_read_close_and_broken_source_cleanup(self):
        path = self.new_path("early-close")
        payload = random.Random(2001).randbytes(256 * 1024)
        await self.client.fs.write_stream(path, [payload], block_size=16 * 1024)

        for _ in range(40):
            stream = self.client.fs.read_stream(path, block_size=16 * 1024)
            self.assertTrue(await stream.__anext__())
            await stream.aclose()
            await asyncio.sleep(0.01)
        self.assertEqual(await self.read_all(path), payload)

        broken_path = self.new_path("broken-source")
        prefix = random.Random(2002).randbytes(128 * 1024)

        async def broken_source():
            yield prefix
            raise RuntimeError("intentional source failure")

        with self.assertRaisesRegex(RuntimeError, "intentional source failure"):
            await self.client.fs.write_stream(
                broken_path, broken_source(), block_size=16 * 1024
            )
        await self.wait_for_size(broken_path, len(prefix))

        tail = random.Random(2003).randbytes(32 * 1024 + 1)
        total = await self.client.fs.write_stream(
            broken_path,
            [tail],
            block_size=8192,
            offset=len(prefix),
        )
        self.assertEqual(total, len(prefix) + len(tail))
        self.assertEqual(await self.read_all(broken_path), prefix + tail)
        await self.control_probe()

    async def test_write_cancellation_partial_file_and_recovery(self):
        path = self.new_path("cancel-write")
        prefix = random.Random(3001).randbytes(64 * 1024)
        tail = random.Random(3002).randbytes(64 * 1024 + 3)
        source_started = asyncio.Event()
        gate = asyncio.Event()

        async def stalled_source():
            yield prefix
            source_started.set()
            await gate.wait()
            yield b"unreachable"

        task = asyncio.create_task(
            self.client.fs.write_stream(path, stalled_source(), block_size=16 * 1024)
        )
        await asyncio.wait_for(source_started.wait(), timeout=10)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await self.wait_for_size(path, len(prefix))

        total = await self.client.fs.write_stream(
            path, [tail], block_size=16 * 1024, offset=len(prefix)
        )
        self.assertEqual(total, len(prefix) + len(tail))
        self.assertEqual(await self.read_all(path), prefix + tail)
        await self.control_probe()

    async def test_invalid_offsets_missing_files_and_directory_targets(self):
        path = self.new_path("invalid-offset")
        await self.client.fs.write_stream(path, [b"abc"], block_size=2)
        with self.assertRaises(ProtocolError) as raised:
            await self.client.fs.write_stream(path, [b"bad"], block_size=2, offset=4)
        self.assertEqual(raised.exception.code, -32600)
        self.assertEqual(await self.read_all(path), b"abc")

        missing = self.new_path("missing")
        with self.assertRaises((ProtocolError, FileSystemError)):
            await self.client.fs.write_stream(missing, [b"x"], block_size=1, offset=1)
        with self.assertRaises((ProtocolError, FileSystemError)):
            async for _ in self.client.fs.read_stream(missing):
                pass
        with self.assertRaises((ProtocolError, FileSystemError)):
            await self.client.fs.write_stream(self.target_dir, [b"x"], block_size=1)

    async def test_permission_and_special_file_errors(self):
        path = self.new_path("permission-denied")
        native_path = path.removeprefix("file://")
        await self.client.fs.write_stream(path, [b"original"], block_size=2)
        await self.run_command(["/bin/chmod", "400", native_path])
        try:
            with self.assertRaises((ProtocolError, FileSystemError)):
                await asyncio.wait_for(
                    self.client.fs.write_stream(path, [b"denied"], block_size=2),
                    timeout=15,
                )
            self.assertEqual(await self.read_all(path), b"original")
        finally:
            await self.run_command(["/bin/chmod", "600", native_path])

        if self.expected_uname == "Linux":
            with self.assertRaises((ProtocolError, FileSystemError)):
                await asyncio.wait_for(
                    self.client.fs.write_stream(
                        "file:///dev/full", [b"no space"], block_size=2
                    ),
                    timeout=15,
                )
        await self.control_probe()

    async def test_concurrent_streams_control_and_data_isolation(self):
        paths = [self.new_path(f"concurrent-{index}") for index in range(4)]
        payloads = [
            random.Random(4000 + index).randbytes(256 * 1024 + index)
            for index in range(4)
        ]
        writes = [
            self.client.fs.write_stream(path, [payload], block_size=16 * 1024)
            for path, payload in zip(paths, payloads, strict=True)
        ]
        totals, _ = await asyncio.gather(asyncio.gather(*writes), self.control_probe())
        self.assertEqual(totals, [len(payload) for payload in payloads])

        reads = [self.read_all(path) for path in paths]
        received, _ = await asyncio.gather(asyncio.gather(*reads), self.control_probe())
        for expected, actual in zip(payloads, received, strict=True):
            self.assertEqual(
                hashlib.sha256(actual).digest(), hashlib.sha256(expected).digest()
            )

    async def test_slow_read_consumer_fails_cleanly_and_recovers(self):
        path = self.new_path("slow-reader")
        payload = random.Random(5001).randbytes(32 * 1024 * 1024)
        await self.client.fs.write_stream(path, [payload], block_size=64 * 1024)

        stream = self.client.fs.read_stream(path, block_size=64 * 1024)
        self.assertTrue(await stream.__anext__())
        await asyncio.sleep(2)
        with self.assertRaisesRegex(FileSystemError, "too slow"):
            while True:
                await stream.__anext__()
        await stream.aclose()

        await self.control_probe()
        recovery_path = self.new_path("slow-reader-recovery")
        await self.client.fs.write_stream(recovery_path, [b"recovered"], block_size=2)
        self.assertEqual(await self.read_all(recovery_path), b"recovered")

    async def test_multiple_harness_clients_share_environment_without_crosstalk(self):
        other = self.make_client(
            os.environ["NOVA_RELAY_LIVE_ENVIRONMENT_ID"], self.token
        )
        await other.connect()
        other_path = f"{self.target_dir}/nova-live-other-client-{uuid.uuid4().hex}.bin"
        own_path = self.new_path("own-client")
        own_payload = random.Random(5101).randbytes(2 * 1024 * 1024 + 1)
        other_payload = random.Random(5102).randbytes(2 * 1024 * 1024 + 2)
        try:
            totals, _ = await asyncio.gather(
                asyncio.gather(
                    self.client.fs.write_stream(
                        own_path, [own_payload], block_size=64 * 1024
                    ),
                    other.fs.write_stream(
                        other_path, [other_payload], block_size=64 * 1024
                    ),
                ),
                self.control_probe(),
            )
            self.assertEqual(totals, [len(own_payload), len(other_payload)])

            async def read_other(path):
                chunks = bytearray()
                async for chunk in other.fs.read_stream(path, block_size=64 * 1024):
                    chunks.extend(chunk)
                return bytes(chunks)

            received = await asyncio.gather(
                self.read_all(own_path, block_size=64 * 1024),
                read_other(other_path),
            )
            self.assertEqual(received[0], own_payload)
            self.assertEqual(received[1], other_payload)
        finally:
            try:
                await asyncio.wait_for(other.fs.remove(other_path), timeout=5)
            except Exception:
                pass
            await asyncio.wait_for(other.disconnect(), timeout=15)

    async def test_cross_environment_isolation_and_token_scope(self):
        alt_environment = os.environ.get("NOVA_RELAY_LIVE_ALT_ENVIRONMENT_ID")
        alt_token_path = os.environ.get("NOVA_RELAY_LIVE_ALT_TOKEN_PATH")
        alt_target_dir = os.environ.get("NOVA_RELAY_LIVE_ALT_TARGET_DIR")
        if not all((alt_environment, alt_token_path, alt_target_dir)):
            self.skipTest("未配置 NOVA_RELAY_LIVE_ALT_* 第二环境")
        alt_token = json.loads(Path(alt_token_path).read_text())["harness_token"]
        alt = self.make_client(alt_environment, alt_token)
        await alt.connect()
        alt_path = f"{alt_target_dir.rstrip('/')}/nova-live-alt-{uuid.uuid4().hex}.bin"
        own_path = self.new_path("primary-environment")
        own_payload = random.Random(5201).randbytes(1024 * 1024 + 1)
        alt_payload = random.Random(5202).randbytes(1024 * 1024 + 2)
        try:
            await asyncio.gather(
                self.client.fs.write_stream(
                    own_path, [own_payload], block_size=32 * 1024
                ),
                alt.fs.write_stream(alt_path, [alt_payload], block_size=32 * 1024),
            )

            async def read_alt(path):
                chunks = bytearray()
                async for chunk in alt.fs.read_stream(path, block_size=32 * 1024):
                    chunks.extend(chunk)
                return bytes(chunks)

            own_received, alt_received = await asyncio.gather(
                self.read_all(own_path), read_alt(alt_path)
            )
            self.assertEqual(own_received, own_payload)
            self.assertEqual(alt_received, alt_payload)
        finally:
            try:
                await asyncio.wait_for(alt.fs.remove(alt_path), timeout=5)
            except Exception:
                pass
            await asyncio.wait_for(alt.disconnect(), timeout=15)

        wrong = self.make_client(alt_environment, self.token)
        try:
            with self.assertRaises((AuthError, TransportError, ProtocolError)):
                await asyncio.wait_for(wrong.connect(), timeout=20)
        finally:
            await asyncio.wait_for(wrong.disconnect(), timeout=15)

    async def test_configurable_mixed_traffic_soak(self):
        rounds = int(os.environ.get("NOVA_RELAY_LIVE_SOAK_ROUNDS", "0"))
        if rounds <= 0:
            self.skipTest("soak 需设置 NOVA_RELAY_LIVE_SOAK_ROUNDS")
        started = asyncio.get_running_loop().time()
        total_bytes = 0
        max_control_seconds = 0.0
        for index in range(rounds):
            path = self.new_path(f"soak-{index}")
            size = (index % 4 + 1) * 512 * 1024 + index
            payload = random.Random(5300 + index).randbytes(size)
            total = await self.client.fs.write_stream(
                path, [payload], block_size=64 * 1024
            )
            self.assertEqual(total, size)
            received = await self.read_all(path, block_size=64 * 1024)
            self.assertEqual(
                hashlib.sha256(received).digest(), hashlib.sha256(payload).digest()
            )
            control_started = asyncio.get_running_loop().time()
            await self.control_probe()
            max_control_seconds = max(
                max_control_seconds, asyncio.get_running_loop().time() - control_started
            )
            total_bytes += size
            await self.client.fs.remove(path)
            self.cleanup_paths.remove(path)
        print(
            f"soak rounds={rounds} bytes={total_bytes} "
            f"elapsed={asyncio.get_running_loop().time() - started:.2f}s "
            f"max_control={max_control_seconds:.3f}s",
            flush=True,
        )

    async def test_relay_restart_process_session_recovery(self):
        self.require_fault_command()
        handle = await self.client.process.start(
            argv=[
                self.shell,
                "-c",
                "i=0; while [ $i -lt 20 ]; do printf 'relay-restart-process-%s\\n' \"$i\"; i=$((i+1)); sleep 1; done",
            ],
            cwd=self.cwd,
        )
        chunks = []
        first = await handle.read(wait_ms=1000)
        chunks.extend(first.chunks)
        self.assertFalse(first.exited)
        self.assertIn(b"relay-restart-process-0", b"".join(chunks))

        await self.inject_fault()
        while True:
            response = await asyncio.wait_for(handle.read(wait_ms=1000), timeout=45)
            chunks.extend(response.chunks)
            if response.exited:
                self.assertEqual(response.exit_code, 0, (response, b"".join(chunks)))
                break
        output = b"".join(chunks)
        self.assertIn(b"relay-restart-process-19", output)
        await self.wait_control_ready()
        await self.control_probe()

    async def test_relay_restart_during_resumable_write(self):
        self.require_fault_command()
        path = self.new_path("restart-resumable-write")
        payload = random.Random(5501).randbytes(64 * 1024 * 1024 + 31)
        writer = asyncio.create_task(
            self.client.fs.write_stream_resumable(
                path,
                payload,
                block_size=64 * 1024,
                max_resume_attempts=8,
            )
        )
        await asyncio.sleep(0.1)
        await self.inject_fault()
        total = await asyncio.wait_for(writer, timeout=180)
        self.assertEqual(total, len(payload))
        self.assertEqual(await self.read_all(path, block_size=64 * 1024), payload)
        await self.control_probe()

    async def test_relay_restart_read_stream_fails_loudly_then_reopens(self):
        self.require_fault_command()
        path = self.new_path("restart-read-stream")
        payload = random.Random(5601).randbytes(32 * 1024 * 1024 + 17)
        await self.client.fs.write_stream(path, [payload], block_size=64 * 1024)

        stream = self.client.fs.read_stream(path, block_size=64 * 1024)
        self.assertTrue(await stream.__anext__())
        fault = asyncio.create_task(self.inject_fault())
        try:
            with self.assertRaises(FileSystemError) as raised:
                while True:
                    await asyncio.wait_for(stream.__anext__(), timeout=45)
            self.assertRegex(
                str(raised.exception), "disconnected|failed to resume|transport closed"
            )
        finally:
            await fault
        await stream.aclose()

        await self.wait_control_ready()
        reopened = await asyncio.wait_for(
            self.read_all(path, block_size=64 * 1024), timeout=60
        )
        self.assertEqual(reopened, payload)
        await self.control_probe()

    @unittest.skipUnless(
        os.environ.get("NOVA_RELAY_LIVE_LONG") == "1",
        "长时 keepalive 测试需 NOVA_RELAY_LIVE_LONG=1",
    )
    async def test_long_lived_write_stream_keepalive_and_control(self):
        path = self.new_path("keepalive")
        block = random.Random(6001).randbytes(64 * 1024)

        async def slow_source():
            for _ in range(12):
                yield block
                await asyncio.sleep(10)

        writer = asyncio.create_task(
            self.client.fs.write_stream(path, slow_source(), block_size=64 * 1024)
        )
        while not writer.done():
            await self.control_probe()
            if not writer.done():
                await asyncio.sleep(10)
        self.assertEqual(await writer, 12 * len(block))
        self.assertEqual(await self.read_all(path), block * 12)

    async def test_sandboxed_streams_and_symlink_escape(self):
        workspace = os.environ.get("NOVA_RELAY_LIVE_SANDBOX_WORKSPACE")
        workspace_uri = os.environ.get("NOVA_RELAY_LIVE_SANDBOX_WORKSPACE_URI")
        outside = os.environ.get("NOVA_RELAY_LIVE_SANDBOX_OUTSIDE")
        outside_uri = os.environ.get("NOVA_RELAY_LIVE_SANDBOX_OUTSIDE_URI")
        if not all((workspace, workspace_uri, outside, outside_uri)):
            self.skipTest("未配置沙箱真机目录")

        await self.run_command([self.shell, "-c", f"mkdir -p {shlex.quote(workspace)}"])
        sandbox = FileSystemSandboxContext.workspace_write(workspace)
        inside = f"{workspace_uri.rstrip('/')}/nova-live-inside-{uuid.uuid4().hex}.bin"
        self.cleanup_paths.append(inside)
        payload = random.Random(7001).randbytes(256 * 1024 + 1)

        total = await self.client.fs.write_stream(
            inside, [payload], block_size=16 * 1024, sandbox=sandbox
        )
        self.assertEqual(total, len(payload))
        self.assertEqual(await self.read_all(inside, sandbox=sandbox), payload)
        read_only = FileSystemSandboxContext.read_only(workspace)
        self.assertEqual(await self.read_all(inside, sandbox=read_only), payload)
        with self.assertRaises((ProtocolError, FileSystemError)):
            await self.client.fs.write_stream(
                inside, [b"denied"], block_size=2, sandbox=read_only
            )

        self.cleanup_paths.append(outside_uri)
        with self.assertRaises((ProtocolError, FileSystemError)):
            await self.client.fs.write_stream(
                outside_uri, [b"denied"], block_size=2, sandbox=sandbox
            )

        await self.client.fs.write_file(outside_uri, b"original")
        symlink = f"{workspace}/nova-live-link-{uuid.uuid4().hex}"
        symlink_uri = f"{workspace_uri.rstrip('/')}/{Path(symlink).name}"
        self.cleanup_paths.append(symlink_uri)
        await self.run_command(["/bin/ln", "-s", outside, symlink], cwd="file:///tmp")
        with self.assertRaises((ProtocolError, FileSystemError)):
            await self.client.fs.write_stream(
                symlink_uri, [b"escape"], block_size=2, sandbox=sandbox
            )
        self.assertEqual(await self.client.fs.read_file(outside_uri), b"original")


if __name__ == "__main__":
    unittest.main()
