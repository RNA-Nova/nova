"""便携 grep 引擎（_collect_with_walk）的有界并发流水线测试。

覆盖：并发窗口真实生效（时延摊薄）、结果保序（与目标序一致）、
limit 提前收口（不再读剩余文件）、abort 中断。
"""

import asyncio
import time

from nova_coding_agent.tools_common.fs_layer import FsStat, WalkItem, WalkResult
from nova_coding_agent.tools_common.operations import (
    _SEARCH_CONCURRENCY,
    GrepOptions,
    LocalGrepOperations,
)


class _SlowLayer:
    """带读取时延与计数的内存 layer（并发证明用）。"""

    accelerates_search = False

    def __init__(self, files, delay=0.05):
        self.files = files
        self.delay = delay
        self.read_count = 0

    async def read_bytes(self, path):
        self.read_count += 1
        await asyncio.sleep(self.delay)
        return self.files[path]

    async def metadata(self, path):
        return FsStat(exists=True, is_dir=True)

    async def walk(self, path, *, max_entries=50_000):
        return WalkResult(
            entries=tuple(WalkItem(path=p, is_dir=False) for p in self.files)
        )


def _run(coro):
    return asyncio.run(coro)


def _options(**kw):
    return GrepOptions(pattern="hit", **kw)


class TestPortableGrepConcurrency:
    def test_concurrent_reads_compress_latency(self):
        files = {f"/r/f{i:02}.txt": f"hit {i}\n".encode() for i in range(16)}
        layer = _SlowLayer(files, delay=0.05)
        ops = LocalGrepOperations(layer)
        started = time.monotonic()
        matches, limit_reached = _run(ops._collect_with_walk("/r", True, _options()))
        elapsed = time.monotonic() - started
        # 串行 = 16 × 50ms = 800ms；窗口 8 并发 ≈ 2 波 ≈ 100ms 量级
        assert elapsed < 0.4, f"并发未生效（{elapsed:.2f}s ≈ 串行）"
        assert len(matches) == 16
        assert limit_reached is False

    def test_results_preserve_target_order(self):
        files = {f"/r/f{i:02}.txt": f"hit {i}\n".encode() for i in range(12)}
        layer = _SlowLayer(files, delay=0.02)
        ops = LocalGrepOperations(layer)
        matches, _ = _run(ops._collect_with_walk("/r", True, _options()))
        assert [m.path for m in matches] == sorted(files.keys())
        assert [m.text for m in matches] == [f"hit {i}" for i in range(12)]

    def test_limit_stops_reading_remaining_files(self):
        files = {f"/r/f{i:02}.txt": b"hit\n" for i in range(20)}
        layer = _SlowLayer(files, delay=0.02)
        ops = LocalGrepOperations(layer)
        matches, limit_reached = _run(
            ops._collect_with_walk("/r", True, _options(limit=1))
        )
        assert len(matches) == 1 and limit_reached is True
        # 窗口预填 + 首个 drain 命中即返回——不读完全部 20 个
        assert layer.read_count <= _SEARCH_CONCURRENCY + 1

    def test_abort_interrupts(self):
        files = {f"/r/f{i}.txt": b"hit\n" for i in range(8)}
        layer = _SlowLayer(files, delay=0.2)
        ops = LocalGrepOperations(layer)

        class _Signal:
            aborted = True

        try:
            _run(ops._collect_with_walk("/r", True, _options(signal=_Signal())))
            raise AssertionError("should abort")
        except RuntimeError as exc:
            assert "aborted" in str(exc)
