"""cache_stats 缓存浪费分析测试（语义对齐 pi core/cache-stats.ts）。"""

from types import SimpleNamespace

from nova_ai import AssistantMessage
from nova_ai.types.model import Cost, Usage

from nova_harness.core.agent_session.controllers.stats import StatsCollector
from nova_harness.core.harness.session.cache_stats import (
    NOISE_FLOOR_TOKENS,
    collect_cache_misses,
    compute_cache_waste,
    detect_cache_miss,
)
from nova_harness.core.types.session.entries import (
    CompactionEntry,
    SessionMessageEntry,
)

T0 = 1_700_000_000_000


def _assistant(
    *,
    input: int = 0,
    output: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
    cost: Cost | None = None,
    provider: str = "anthropic",
    model: str = "claude",
    timestamp: int = T0,
) -> AssistantMessage:
    return AssistantMessage(
        provider=provider,
        model=model,
        usage=Usage(
            input=input,
            output=output,
            cache_read=cache_read,
            cache_write=cache_write,
            cost=cost or Cost(),
        ),
        timestamp=timestamp,
    )


def _entry(message: AssistantMessage) -> SessionMessageEntry:
    return SessionMessageEntry(message=message)


class _FakePriceSource:
    """find(provider, model_id) -> 带 cost.cache_read（$/百万 tokens）的对象。"""

    def __init__(self, cache_read_rate: float) -> None:
        self._rate = cache_read_rate

    def find(self, provider: str, model_id: str):
        return SimpleNamespace(cost=SimpleNamespace(cache_read=self._rate))


def test_first_turn_has_no_miss() -> None:
    entries = [_entry(_assistant(input=10_000, cache_write=10_000))]
    totals = compute_cache_waste(entries)
    assert totals.miss_count == 0
    assert totals.missed_tokens == 0


def test_miss_counted_when_cache_read_low() -> None:
    # 第一轮写缓存 100k；第二轮 prompt 100k 但只读了 50k 缓存 → miss 50k
    entries = [
        _entry(_assistant(input=1_000, cache_write=100_000, timestamp=T0)),
        _entry(_assistant(input=50_000, cache_read=50_000, timestamp=T0 + 60_000)),
    ]
    totals = compute_cache_waste(entries)
    assert totals.miss_count == 1
    assert totals.missed_tokens == 50_000

    misses = collect_cache_misses(entries)
    assert set(misses.keys()) == {1}
    assert misses[1].idle_ms == 60_000
    assert misses[1].model_changed is False


def test_noise_floor_suppresses_small_misses() -> None:
    entries = [
        _entry(_assistant(input=1_000, cache_write=2_000, timestamp=T0)),
        _entry(
            _assistant(
                input=1_000,
                cache_read=2_000 - NOISE_FLOOR_TOKENS,
                timestamp=T0 + 1_000,
            )
        ),
    ]
    assert compute_cache_waste(entries).miss_count == 0


def test_compaction_resets_baseline() -> None:
    entries = [
        _entry(_assistant(input=1_000, cache_write=100_000, timestamp=T0)),
        CompactionEntry(),
        # 压缩后第一轮 cache 读为 0 是合法的（上下文已变更），不应计 miss
        _entry(_assistant(input=20_000, timestamp=T0 + 1_000)),
    ]
    assert compute_cache_waste(entries).miss_count == 0


def test_provider_without_cache_reporting_never_counts() -> None:
    # 两轮 cache_read/cache_write 全为 0 且之前从未报告缓存活动 → 不计
    entries = [
        _entry(_assistant(input=100_000, timestamp=T0)),
        _entry(_assistant(input=100_000, timestamp=T0 + 1_000)),
    ]
    assert compute_cache_waste(entries).miss_count == 0


def test_cache_read_only_provider_total_miss() -> None:
    # OpenAI 式 provider：写缓存不上报。第一轮有 cache_write → reportedCache
    # 粘性标记；第二轮读+写为 0 → 全量 miss。
    entries = [
        _entry(_assistant(input=1_000, cache_write=50_000, timestamp=T0)),
        _entry(_assistant(input=50_000, timestamp=T0 + 1_000)),
    ]
    totals = compute_cache_waste(entries)
    assert totals.miss_count == 1
    assert totals.missed_tokens == 50_000


def test_model_change_is_counted_and_flagged() -> None:
    entries = [
        _entry(_assistant(input=1_000, cache_write=50_000, timestamp=T0)),
        _entry(
            _assistant(
                input=50_000,
                provider="openai",
                model="gpt",
                timestamp=T0 + 1_000,
            )
        ),
    ]
    misses = collect_cache_misses(entries)
    assert misses[1].model_changed is True
    assert misses[1].missed_tokens == 50_000


def test_missed_cost_uses_own_cost_breakdown() -> None:
    # 第二轮：付费 50k input 花了 $0.25（$5/百万），缓存读 50k 花了 $0.025
    # （$0.5/百万）。missed=50k → 多付 50k * (5e-6 - 5e-7) = $0.225
    entries = [
        _entry(_assistant(input=1_000, cache_write=100_000, timestamp=T0)),
        _entry(
            _assistant(
                input=50_000,
                cache_read=50_000,
                cost=Cost(input=0.25, cache_read=0.025, total=0.275),
                timestamp=T0 + 1_000,
            )
        ),
    ]
    totals = compute_cache_waste(entries)
    assert totals.miss_count == 1
    assert abs(totals.missed_cost - 0.225) < 1e-9


def test_missed_cost_falls_back_to_price_source() -> None:
    # cache_read=0 时读费率查价格源：$0.5/百万 → 5e-7/token
    entries = [
        _entry(_assistant(input=1_000, cache_write=50_000, timestamp=T0)),
        _entry(
            _assistant(
                input=50_000,
                cost=Cost(input=0.25, total=0.25),
                timestamp=T0 + 1_000,
            )
        ),
    ]
    totals = compute_cache_waste(entries, models=_FakePriceSource(0.5))
    # 付费费率 0.25/50k = 5e-6；读费率 5e-7 → 50k * 4.5e-6 = 0.225
    assert abs(totals.missed_cost - 0.225) < 1e-9


def test_missed_cost_zero_without_pricing() -> None:
    # 无定价且消息自身无成本明细 → missed_cost 为 0（定价未知）
    entries = [
        _entry(_assistant(input=1_000, cache_write=50_000, timestamp=T0)),
        _entry(_assistant(input=50_000, timestamp=T0 + 1_000)),
    ]
    totals = compute_cache_waste(entries)
    assert totals.miss_count == 1
    assert totals.missed_cost == 0.0


def test_detect_cache_miss_for_new_message() -> None:
    entries = [_entry(_assistant(input=1_000, cache_write=50_000, timestamp=T0))]
    new_message = _assistant(input=50_000, timestamp=T0 + 30_000)
    miss = detect_cache_miss(entries, new_message)
    assert miss is not None
    assert miss.missed_tokens == 50_000
    assert miss.idle_ms == 30_000


def test_detect_cache_miss_returns_none_for_first_turn() -> None:
    miss = detect_cache_miss([], _assistant(input=50_000, timestamp=T0))
    assert miss is None


def test_stats_collector_get_cache_waste() -> None:
    entries = [
        _entry(_assistant(input=1_000, cache_write=100_000, timestamp=T0)),
        _entry(_assistant(input=50_000, cache_read=0, timestamp=T0 + 1_000)),
    ]
    fake_session = SimpleNamespace(
        session_manager=SimpleNamespace(get_entries=lambda: entries)
    )
    collector = StatsCollector(fake_session)
    totals = collector.get_cache_waste()
    assert totals.miss_count == 1
    assert totals.missed_tokens == 50_000
