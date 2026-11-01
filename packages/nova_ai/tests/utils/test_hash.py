"""short_hash 测试（确定性短哈希——tool_call id 超 40 字符截断的唯一依赖）。

生产消费点：``openai_completions/messages.py`` 的 ``normalize_tool_call_id``
（管道形态 id 超长时 ``short_hash(id)[:8]`` 截断）——钉死算法行为即钉死
tool_call 配对完整性。
"""

from nova_ai.utils.hash import short_hash


def test_golden_vectors():
    """金标值钉死算法（防重构/移植漂移——BMP 内与 TS shortHash 同输入同输出）。"""
    assert short_hash("abc") == "y0biex7f9bbh"
    assert short_hash("") == "k4n83c7h0j2b"
    assert short_hash("call_abc123|item_xyz789") == "112sdcg7iqv17"


def test_deterministic():
    assert short_hash("hello") == short_hash("hello")


def test_output_is_base36():
    digest = short_hash("some tool call id")
    assert digest
    assert all(c in "0123456789abcdefghijklmnopqrstuvwxyz" for c in digest)


def test_distinct_inputs_diverge():
    """同 call_id 不同 item_id（Responses 管道形态的真实撞脸场景）哈希不同。"""
    base = "call_" + "x" * 50
    assert short_hash(f"{base}|itemA") != short_hash(f"{base}|itemB")
