"""Prompt 缓存浪费分析（对齐 pi ``core/cache-stats.ts``）。

扫描会话 entries，把"上一轮 prompt 中已出现、本轮却没有走缓存读取"的 token
计为缓存浪费（missed tokens），并按消息自身的费率明细折算成多付的成本
（missed cost）。产出三个接口：

- ``compute_cache_waste``：会话级浪费汇总；
- ``collect_cache_misses``：逐条 assistant 消息的 miss 表（以 entry 下标为键，
  供 resume / compaction 后重建提示；pi 按消息引用键控，Python pydantic 消息
  不可哈希，故改用下标——语义等价，消费方按下标对齐 entries 即可）；
- ``detect_cache_miss``：单条新完成 assistant 消息的即时检测（驱动 transcript
  中的 cache-miss 提醒，由 settings ``show_cache_miss_notices`` 门控）。

设计细节（与 pi 逐项一致）：

- 参考 TTL 5 分钟（Anthropic 默认缓存 TTL），空闲更久的 miss 可归因于过期；
- 噪声地板 1024 token：小于缓存断点粒度的 miss 不计；
- compaction / branch_summary 条目后重置基线（上下文已合法变更）；
- 模型切换**不豁免**（切换导致全量重计费，应计入），仅标记 ``model_changed``；
- 区分 cache-read-only provider（OpenAI 式，写缓存不上报）与从不报缓存的
  provider：前者曾见过缓存活动后，本轮 cache 读+写为 0 算全量 miss；后者
  永远不计。
"""

from typing import Dict, List, Optional, Tuple

from nova_ai import Message

from nova_harness.core.types.session.entries import SessionEntry
from nova_harness.core.types.session.stats import (
    CacheMiss,
    CacheWasteTotals,
    ModelPriceSource,
)

# Prompt 缓存参考 TTL：空闲超过该间隔的 miss 多半可归因于缓存过期
# （Anthropic 默认缓存 TTL 为 5 分钟）。
CACHE_TTL_MS = 5 * 60 * 1000

# 单轮 miss 低于该值视为缓存断点粒度噪声，不计入。
NOISE_FLOOR_TOKENS = 1024


class _PreviousRequest:
    """扫描中看到的上一次请求；其 prompt 中的内容本应全部命中缓存。"""

    __slots__ = ("prompt_tokens", "model_key", "timestamp", "reported_cache")

    def __init__(
        self,
        prompt_tokens: int,
        model_key: str,
        timestamp: int,
        reported_cache: bool,
    ) -> None:
        self.prompt_tokens = prompt_tokens
        self.model_key = model_key
        self.timestamp = timestamp
        # 粘性标记：本扫描段内曾有请求报告过缓存活动。用于区分
        # cache-read-only provider（写缓存不上报，本轮读+写为 0 即全量 miss）
        # 与从不报告缓存的 provider（读+写为 0 不代表任何信息）。
        self.reported_cache = reported_cache


def _message_usage(message) -> Tuple[int, int, int]:
    """返回 ``(input, cache_read, cache_write)``；非 assistant 消息返回全零。"""
    usage = getattr(message, "usage", None)
    if usage is None:
        return 0, 0, 0
    return (
        getattr(usage, "input", 0) or 0,
        getattr(usage, "cache_read", 0) or 0,
        getattr(usage, "cache_write", 0) or 0,
    )


def _detect_miss(
    prev: Optional[_PreviousRequest],
    message,
    models: Optional[ModelPriceSource],
) -> Optional[CacheMiss]:
    """计算单条 assistant 消息相对上一请求的缓存 miss。

    返回 ``None`` 表示不计：首轮、重置后、provider 从不报告缓存活动、
    或 miss 低于噪声地板。
    """
    input_tokens, cache_read, cache_write = _message_usage(message)
    prompt_tokens = input_tokens + cache_read + cache_write
    # cache 读+写全为 0 的轮次，只有在之前报告过缓存活动时才有意义：
    # cache-read-only provider 上那是全量 miss；从不报告缓存的 provider
    # 上则什么都不是。
    if (
        prev is None
        or prompt_tokens <= 0
        or (cache_read + cache_write == 0 and not prev.reported_cache)
    ):
        return None

    missed_tokens = min(prev.prompt_tokens, prompt_tokens) - cache_read
    if missed_tokens <= NOISE_FLOOR_TOKENS:
        return None

    # 多付成本 = missed tokens 按实际付费费率（input/cacheWrite，含写缓存
    # 溢价）计费与按缓存读费率计费之差。missed tokens 只会落在 input 或
    # cacheWrite 桶里，因此付费费率直接取自本条消息自身的成本明细。
    usage = message.usage
    paid_tokens = input_tokens + cache_write
    if paid_tokens > 0:
        paid_per_token = (usage.cost.input + usage.cost.cache_write) / paid_tokens
    else:
        paid_per_token = 0.0
    if cache_read > 0:
        read_per_token = usage.cost.cache_read / cache_read
    else:
        model = (
            models.find(getattr(message, "provider", ""), getattr(message, "model", ""))
            if models is not None
            else None
        )
        cache_read_rate = (
            getattr(getattr(model, "cost", None), "cache_read", 0.0) or 0.0
        )
        read_per_token = cache_read_rate / 1_000_000

    timestamp = getattr(message, "timestamp", 0) or 0
    model_key = f"{getattr(message, 'provider', '')}/{getattr(message, 'model', '')}"
    return CacheMiss(
        missed_tokens=missed_tokens,
        missed_cost=missed_tokens * max(0.0, paid_per_token - read_per_token),
        idle_ms=max(0, timestamp - prev.timestamp),
        model_changed=model_key != prev.model_key,
    )


def _as_previous_request(message, reported_cache: bool) -> Optional[_PreviousRequest]:
    input_tokens, cache_read, cache_write = _message_usage(message)
    prompt_tokens = input_tokens + cache_read + cache_write
    if prompt_tokens <= 0:
        return None
    return _PreviousRequest(
        prompt_tokens=prompt_tokens,
        model_key=f"{getattr(message, 'provider', '')}/{getattr(message, 'model', '')}",
        timestamp=getattr(message, "timestamp", 0) or 0,
        reported_cache=reported_cache or (cache_read + cache_write > 0),
    )


def _is_assistant_message(message) -> bool:
    return getattr(message, "role", None) == "assistant"


def _scan(
    entries: List[SessionEntry],
    models: Optional[ModelPriceSource],
) -> Tuple[Optional[_PreviousRequest], CacheWasteTotals, Dict[int, CacheMiss]]:
    prev: Optional[_PreviousRequest] = None
    totals = CacheWasteTotals()
    misses: Dict[int, CacheMiss] = {}

    for index, entry in enumerate(entries):
        entry_type = getattr(entry, "type", None)
        if entry_type in ("compaction", "branch_summary"):
            # 上下文已合法变更：下一轮的 prompt 是新内容而非被重复计费的
            # 旧内容。模型切换不豁免——它会重计费整份 prompt，照常计入。
            prev = None
            continue
        if entry_type != "message":
            continue
        message = getattr(entry, "message", None)
        if not _is_assistant_message(message):
            continue
        miss = _detect_miss(prev, message, models)
        if miss is not None:
            totals.missed_tokens += miss.missed_tokens
            totals.missed_cost += miss.missed_cost
            totals.miss_count += 1
            misses[index] = miss
        prev = (
            _as_previous_request(
                message, prev.reported_cache if prev is not None else False
            )
            or prev
        )

    return prev, totals, misses


def compute_cache_waste(
    entries: List[SessionEntry],
    models: Optional[ModelPriceSource] = None,
) -> CacheWasteTotals:
    """会话级缓存浪费：上一轮 prompt 已出现、却被重新计费而非缓存读取的 token。"""
    return _scan(entries, models)[1]


def collect_cache_misses(
    entries: List[SessionEntry],
    models: Optional[ModelPriceSource] = None,
) -> Dict[int, CacheMiss]:
    """会话内全部计及的缓存 miss，以 assistant 消息所在 entry 的下标为键。

    用于从 entries 重建 transcript 提示（resume、compaction 后重建）。
    """
    return _scan(entries, models)[2]


def detect_cache_miss(
    entries: List[SessionEntry],
    message: Message,
    models: Optional[ModelPriceSource] = None,
) -> Optional[CacheMiss]:
    """检测一条刚完成的 assistant 消息上的缓存 miss。

    ``entries`` 必须**尚未**包含该 message（message_end 事件先于持久化触发）。
    """
    prev, _, _ = _scan(entries, models)
    return _detect_miss(prev, message, models)


__all__ = [
    "CACHE_TTL_MS",
    "NOISE_FLOOR_TOKENS",
    "CacheMiss",
    "CacheWasteTotals",
    "ModelPriceSource",
    "compute_cache_waste",
    "collect_cache_misses",
    "detect_cache_miss",
]
