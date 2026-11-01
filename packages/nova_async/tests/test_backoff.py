"""``backoff.py`` 测试（时间族·退避序列）。

纯函数断言（jitter 经 monkeypatch 钉死 random 或采样断言区间）——
不睡觉（testing-modeling §八）。
"""

import pytest
from nova_async import backoff_delay_ms


def test_sequence_is_one_indexed_exponential():
    """jitter=0 的精确序列：base * 2**(attempt-1)，1-indexed。"""
    assert backoff_delay_ms(1, base_ms=500, cap_ms=8000, jitter=0) == 500
    assert backoff_delay_ms(2, base_ms=500, cap_ms=8000, jitter=0) == 1000
    assert backoff_delay_ms(3, base_ms=500, cap_ms=8000, jitter=0) == 2000
    assert backoff_delay_ms(4, base_ms=500, cap_ms=8000, jitter=0) == 4000


def test_cap_clamps():
    """封顶：指数涨到 cap 即饱和。"""
    assert backoff_delay_ms(5, base_ms=500, cap_ms=8000, jitter=0) == 8000
    assert backoff_delay_ms(20, base_ms=500, cap_ms=8000, jitter=0) == 8000


def test_attempt_saturates_below_one():
    """attempt < 1 按 1 算（对位 codex saturating_sub(1)）。"""
    first = backoff_delay_ms(1, base_ms=500, cap_ms=8000, jitter=0)
    assert backoff_delay_ms(0, base_ms=500, cap_ms=8000, jitter=0) == first
    assert backoff_delay_ms(-3, base_ms=500, cap_ms=8000, jitter=0) == first


def test_jitter_down_only_bounds(monkeypatch):
    """down-only 抖动：random()=0 取满额上界；0.5 取 (1-jitter/2)。"""
    monkeypatch.setattr("random.random", lambda: 0.0)
    assert backoff_delay_ms(1, base_ms=1000, cap_ms=8000) == 1000  # 抖 0%
    monkeypatch.setattr("random.random", lambda: 0.5)
    # 默认 jitter=0.25：1000 * (1 - 0.5*0.25) = 875
    assert backoff_delay_ms(1, base_ms=1000, cap_ms=8000) == pytest.approx(875)


def test_jitter_samples_stay_in_interval():
    """采样断言：全部落在 (base*(1-jitter), base]（random() 取不到 1）。"""
    base = backoff_delay_ms(2, base_ms=500, cap_ms=8000, jitter=0)  # 1000
    for _ in range(200):
        value = backoff_delay_ms(2, base_ms=500, cap_ms=8000, jitter=0.25)
        assert base * 0.75 < value <= base


def test_jitter_out_of_domain_raises():
    """jitter 合法域 [0, 1)：负数与 1 一律 ValueError。"""
    with pytest.raises(ValueError):
        backoff_delay_ms(1, base_ms=500, cap_ms=8000, jitter=-0.1)
    with pytest.raises(ValueError):
        backoff_delay_ms(1, base_ms=500, cap_ms=8000, jitter=1.0)
