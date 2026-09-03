"""快速确定性哈希（对齐 TS ``src/utils/hash.ts`` 的 ``shortHash``）。

用于截断超长字符串（如 Responses API 的 ``{call_id}|{item_id}`` 工具调用 id）。
32 位 imul 语义逐位复刻 JS ``Math.imul``，保证与 TS 侧同输入同输出。
"""

_CHARS = "0123456789abcdefghijklmnopqrstuvwxyz"


def _imul32(a: int, b: int) -> int:
    """32 位有符号乘法的无符号位模式（对齐 Math.imul 的位级行为）。"""
    return (a * b) & 0xFFFFFFFF


def _urshift(x: int, n: int) -> int:
    """无符号右移（对齐 JS >>> ）。"""
    return (x & 0xFFFFFFFF) >> n


def _to_base36(value: int) -> str:
    value &= 0xFFFFFFFF
    if value == 0:
        return "0"
    digits = []
    while value:
        value, rem = divmod(value, 36)
        digits.append(_CHARS[rem])
    return "".join(reversed(digits))


def short_hash(text: str) -> str:
    """确定性短哈希（base36 输出，与 TS shortHash 同输入同输出）。"""
    h1 = 0xDEADBEEF
    h2 = 0x41C6CE57
    for ch in text:
        code = ord(ch)
        h1 = _imul32(h1 ^ code, 2654435761)
        h2 = _imul32(h2 ^ code, 1597334677)
    h1 = _imul32(h1 ^ _urshift(h1, 16), 2246822507) ^ _imul32(
        h2 ^ _urshift(h2, 13), 3266489909
    )
    h2 = _imul32(h2 ^ _urshift(h2, 16), 2246822507) ^ _imul32(
        h1 ^ _urshift(h1, 13), 3266489909
    )
    return _to_base36(h2) + _to_base36(h1)


__all__ = ["short_hash"]
