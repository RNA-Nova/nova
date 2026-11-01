"""``deadline.py`` 测试（Deadline 预算值对象——时间族）。

时钟经 monkeypatch 注入（不睡觉——testing-modeling §八）。
"""

import time

import pytest
from nova_async import Deadline


def test_remaining_decreases_with_elapsed(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    deadline = Deadline(timeout_ms=500)
    assert deadline.remaining_ms == pytest.approx(500)
    assert deadline.expired is False

    now[0] += 0.2  # 200ms 后
    assert deadline.remaining_ms == pytest.approx(300)
    assert deadline.expired is False


def test_remaining_floors_at_zero_when_exhausted(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    deadline = Deadline(timeout_ms=100)
    now[0] += 1.0  # 远超预算
    assert deadline.remaining_ms == 0
    assert deadline.expired is True


def test_remaining_s_is_seconds_view(monkeypatch):
    """``remaining_s`` = ``remaining_ms / 1000``（wait_for/asyncio.timeout
    吃秒）；耗尽同样归零不返负。"""
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    deadline = Deadline(timeout_ms=500)
    assert deadline.remaining_s == pytest.approx(0.5)

    now[0] += 0.2  # 200ms 后
    assert deadline.remaining_s == pytest.approx(0.3)

    now[0] += 1.0  # 远超预算
    assert deadline.remaining_s == 0
