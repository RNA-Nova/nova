"""
JSON解析工具
用于解析流式响应中的部分JSON
"""

import json
import re
from typing import Any, Dict, List, Optional, Union

from json_repair import repair_json


def parse_streaming_json(json_str: Optional[str]) -> Union[Dict[str, Any], List[Any]]:
    """
    解析流式响应中的部分JSON

    始终返回一个有效的对象（dict/list），即使JSON不完整。
    标量 JSON（``"5"`` 等）对调用方无意义——归一为 ``{}``，保证返回
    声明的容器形状（本函数的消费面是工具参数片段，恒为 object）。

    Args:
        json_str: 流式响应中的部分JSON字符串

    Returns:
        解析后的对象，如果解析失败则返回空对象
    """
    if not json_str or json_str.strip() == "":
        return {}

    # 首先尝试标准解析（对于完整JSON最快）
    try:
        parsed = json.loads(json_str)
        return parsed if isinstance(parsed, (dict, list)) else {}
    except json.JSONDecodeError:
        pass

    # 使用 json_repair 修复并解析
    try:
        repaired = repair_json(json_str, skip_json_loads=True)
        if repaired:
            parsed = json.loads(repaired)
            return parsed if isinstance(parsed, (dict, list)) else {}
    except Exception:
        pass

    # 如果所有解析都失败，返回空对象
    return {}


# ---------------------------------------------------------------------------
# 增量流式解析器
# ---------------------------------------------------------------------------

_SIMPLE_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}

_NUMBER_RE = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?\Z")
_LITERALS = {"true": True, "false": False, "null": None}
_MISSING = object()  # 不显示标记（半截字面量/非法数字）


def _is_delimiter(char: str) -> bool:
    return char in ' \t\n\r,:{}[]"'


def _is_hex(char: str) -> bool:
    return ("0" <= char <= "9") or ("a" <= char <= "f") or ("A" <= char <= "F")


def _scalar_value(token: str) -> Any:
    """完整标量 token 的值；不合法返回 ``_MISSING``（丢弃）。
    注意 ``null`` 字面量合法且值就是 None——必须用哨兵与"非法"区分。"""
    if token in _LITERALS:
        return _LITERALS[token]
    if _NUMBER_RE.match(token):
        return float(token) if any(c in token for c in ".eE") else int(token)
    return _MISSING


def _pending_scalar_value(token: str) -> Any:
    """在飞标量在快照里的显示值（完整数字/完整字面量才显示，半截不显示）。"""
    return _scalar_value(token)


class _Frame:
    """一个开放容器（栈帧）。"""

    __slots__ = ("container", "slot", "phase", "key")

    def __init__(self, container: Union[Dict[str, Any], List[Any]], slot: Any):
        self.container = container
        self.slot = slot  # 本容器在父里的槽位（快照拷贝路径用）
        self.phase = "key" if isinstance(container, dict) else "value"
        self.key: Optional[str] = None


def _set_slot(
    container: Union[Dict[str, Any], List[Any]], slot: Any, value: Any
) -> None:
    if slot[0] == "element":
        # 槽位已存在则原位更新（在飞字符串逐字符同步），否则追加
        if slot[1] < len(container):  # type: ignore[arg-type]
            container[slot[1]] = value  # type: ignore[index]
        else:
            container.append(value)  # type: ignore[arg-type]
    else:
        container[slot[1]] = value  # type: ignore[index]


class StreamingJsonParser:
    """增量流式 JSON 解析器（工具参数 delta 累积专用）——真增量内核。

    每 delta 成本 O(delta)：手写增量状态机逐字符推进，值完成即就地落进
    结构，不回头读旧文本（旧实现每 delta 对全文跑 ``json.loads``——理论
    O(n²)，C 级常数在 MB 级参数/千 delta 量级会疼）。

    快照纪律：开放容器沿栈拷贝、已完成子树共享——发放后的快照不再变。
    行为契约（与旧实现逐条对齐，测试钉死）：
    - 截断字符串显示已读字符；悬空反斜杠（delta 尾单个 ``\\``）显示字面
      反斜杠（旧伪影同款）；半截 ``\\uXXXX`` 不显示（保留到转义完成）；
    - 开放容器显示已读成员；悬空 token（key 写完值没开始）不进快照；
    - 数字中途显示截断值；半截字面量（``tru``）不显示；
    - 非法输入不炸、快照保持容器形状；
    - ``finish()`` = 全文精确 parse + json_repair 兜底（与
      ``parse_streaming_json`` 一致）。
    """

    def __init__(self) -> None:
        self._text = ""
        self._stack: List[_Frame] = []
        self._root: Any = None
        self._has_root = False
        self._done = False
        # 在飞字符串：{"text", "target"("key"/"value"), "slot", "escape"}
        self._string: Optional[Dict[str, Any]] = None
        # 在飞标量（数字/字面量）缓冲与其槽位
        self._scalar = ""
        self._scalar_slot: Any = None
        self._value: Any = {}
        self._dirty = False

    # ------------------------------------------------------------------
    # 公开面
    # ------------------------------------------------------------------

    @property
    def value(self) -> Any:
        """当前最新解析结果（发放的快照不再变——开放路径拷贝、完成子树共享）。"""
        if not self._dirty:
            return self._value
        self._value = self._make_snapshot()
        self._dirty = False
        return self._value

    def feed(self, delta: str) -> None:
        """追加一段 delta 并刷新解析快照（每 delta 成本 O(delta)）。"""
        self._text += delta
        for char in delta:
            if self._done:
                break
            if self._string is not None:
                self._read_string_char(char)
            elif self._scalar:
                if _is_delimiter(char):
                    self._end_scalar()
                    self._read_structural(char)
                else:
                    self._scalar += char
            else:
                self._read_structural(char)

    def finish(self) -> Any:
        """终态精确解析（块收尾时调用）。终值语义与 parse_streaming_json 一致。"""
        try:
            self._value = json.loads(self._text)
        except ValueError:
            self._value = parse_streaming_json(self._text)
        return self._value

    # ------------------------------------------------------------------
    # 结构层
    # ------------------------------------------------------------------

    def _top(self) -> Optional[_Frame]:
        return self._stack[-1] if self._stack else None

    def _value_slot(self) -> Any:
        """此刻起始的值该去的槽位；非法位置返回 None（读取但丢弃）。"""
        frame = self._top()
        if frame is None:
            return None if self._has_root else ("root",)
        if isinstance(frame.container, list):
            return (
                ("element", len(frame.container))
                if frame.phase in ("value", "comma")
                else None
            )
        # dict：值只在 value 相（或 colon 相——缺冒号容忍）且 key 已就位
        if frame.phase in ("value", "colon") and frame.key is not None:
            return ("member", frame.key)
        return None

    def _place(self, slot: Any, value: Any) -> None:
        if slot is None:
            return
        self._dirty = True
        if slot == ("root",):
            self._root = value
            self._has_root = True
            return
        frame = self._top()
        if frame is not None:
            _set_slot(frame.container, slot, value)

    def _read_structural(self, char: str) -> None:
        if char in " \t\n\r":
            return
        if char == '"':
            self._start_string()
            return
        if char in "{[":
            self._open_container(char)
            return
        if char in "}]":
            self._close_container()
            return
        frame = self._top()
        if char == ":":
            if (
                frame is not None
                and isinstance(frame.container, dict)
                and frame.phase == "colon"
            ):
                frame.phase = "value"
            return
        if char == ",":
            if frame is not None:
                frame.phase = "key" if isinstance(frame.container, dict) else "value"
            return
        # 标量起点
        self._scalar = char
        self._scalar_slot = self._value_slot()
        if self._scalar_slot is not None:
            self._dirty = True  # 在飞标量可显示（快照重建触发）

    def _open_container(self, char: str) -> None:
        container: Union[Dict[str, Any], List[Any]] = {} if char == "{" else []
        slot = self._value_slot()
        if slot is not None:
            self._place(slot, container)
        self._stack.append(_Frame(container, slot))
        self._dirty = True

    def _close_container(self) -> None:
        if not self._stack:
            return  # 游离闭合忽略
        self._stack.pop()
        self._dirty = True
        self._value_ended()

    def _value_ended(self) -> None:
        frame = self._top()
        if frame is None:
            self._done = True  # 根值完成——余下文本忽略
            return
        frame.phase = "comma"

    # ------------------------------------------------------------------
    # 字符串与标量
    # ------------------------------------------------------------------

    def _start_string(self) -> None:
        frame = self._top()
        # key 位：dict 帧的 key/comma 相（缺逗号容忍）
        if (
            frame is not None
            and isinstance(frame.container, dict)
            and frame.phase in ("key", "comma")
        ):
            self._string = {"text": "", "target": "key", "slot": None, "escape": ""}
            return
        slot = self._value_slot()
        self._string = {
            "text": "",
            "target": "value",
            "slot": slot,
            "escape": "",
            "pending_high": None,
        }
        if slot is not None:
            self._place(slot, "")  # 开场即占位——截断字符串逐 delta 可见的地基

    def _read_string_char(self, char: str) -> None:
        s = self._string
        assert s is not None
        escape = s["escape"]
        if escape:
            if escape == "\\":
                if char == "u":
                    s["escape"] = "\\u"
                    return
                decoded = _SIMPLE_ESCAPES.get(char)
                # 非法转义保留字面（json_repair 同款）
                self._flush_pending_high()
                s["text"] += decoded if decoded is not None else "\\" + char
                s["escape"] = ""
            elif _is_hex(char) and len(escape) < 6:
                escape += char
                s["escape"] = escape
                if len(escape) == 6:
                    self._append_code_point(chr(int(escape[2:], 16)))
                    s["escape"] = ""
            else:
                # \u 后非 hex：保留字面，char 落回正常处理
                self._flush_pending_high()
                s["text"] += escape
                s["escape"] = ""
                if char == "\\":
                    s["escape"] = "\\"
                    return
                if char == '"':
                    self._end_string()
                    return
                s["text"] += char
            self._sync_value_string()
            return
        if char == "\\":
            # 新转义开始：不冲刷挂起的高半区代理——它可能与下一个 \uXXXX
            # 低半区合成真实码点（\ud83d\ude00 → 😀）
            s["escape"] = "\\"
            return
        if char == '"':
            self._end_string()
            return
        self._flush_pending_high()
        s["text"] += char
        self._sync_value_string()

    def _append_code_point(self, char: str) -> None:
        """追加一个 \\uXXXX 解码产物——高半区代理先挂起等低半区合成
        （代理对切两个 delta 时序：高半区先解码为孤立代理项——旧实现同款
        瞬态伪影；低半区到达即合成真实码点）。"""
        s = self._string
        assert s is not None
        code = ord(char)
        if 0xD800 <= code <= 0xDBFF:
            self._flush_pending_high()  # 连两个高半区：前一个落为孤立
            s["pending_high"] = char
        elif 0xDC00 <= code <= 0xDFFF and s.get("pending_high") is not None:
            high = ord(s["pending_high"])
            s["text"] += chr(0x10000 + ((high - 0xD800) << 10) + (code - 0xDC00))
            s["pending_high"] = None
        else:
            self._flush_pending_high()
            s["text"] += char

    def _flush_pending_high(self) -> None:
        """挂起的高半区代理落为孤立字符（低半区没来的情形）。"""
        s = self._string
        assert s is not None
        if s.get("pending_high") is not None:
            s["text"] += s["pending_high"]
            s["pending_high"] = None

    def _sync_value_string(self) -> None:
        """值串的半截文本同步进结构（截断可见）。"""
        s = self._string
        assert s is not None
        if s["target"] == "value" and s["slot"] is not None:
            self._place(s["slot"], s["text"])

    def _end_string(self) -> None:
        s = self._string
        assert s is not None
        if s["target"] == "key":
            self._string = None
            frame = self._top()
            if frame is not None and isinstance(frame.container, dict):
                frame.key = s["text"]
                frame.phase = "colon"
            return
        self._flush_pending_high()  # 高半区代理收尾（须先于 self._string 置空）
        if s["slot"] is not None:
            self._place(s["slot"], s["text"])
        self._string = None
        self._value_ended()

    def _end_scalar(self) -> None:
        token = self._scalar
        slot = self._scalar_slot
        self._scalar = ""
        self._scalar_slot = None
        value = _scalar_value(token)
        if value is not _MISSING and slot is not None:
            self._place(slot, value)
        self._value_ended()

    # ------------------------------------------------------------------
    # 快照
    # ------------------------------------------------------------------

    def _pending(self) -> Any:
        """快照时点要显示的在飞值（槽位, 值）；无则 None。"""
        if self._string is not None:
            s = self._string
            if s["target"] == "value" and s["slot"] is not None:
                text = s["text"]
                if s.get("pending_high") is not None:
                    text += s["pending_high"]  # 高半区代理先孤立显示（旧伪影同款）
                if s["escape"] == "\\":
                    text += "\\"  # 悬空反斜杠伪影（旧实现同款）
                return (s["slot"], text)
            return None
        if self._scalar and self._scalar_slot is not None:
            value = _pending_scalar_value(self._scalar)
            if value is not _MISSING:
                return (self._scalar_slot, value)
        return None

    def _make_snapshot(self) -> Any:
        """开放路径拷贝：栈上容器逐帧浅拷（内层嵌进外层拷贝），完成子树共享。"""
        pending = self._pending()
        if not self._stack:
            if pending is not None:
                return pending[1]
            return self._root if self._has_root else {}
        child: Any = None
        child_slot: Any = None
        for depth in range(len(self._stack) - 1, -1, -1):
            frame = self._stack[depth]
            copy: Union[Dict[str, Any], List[Any]] = (
                dict(frame.container)
                if isinstance(frame.container, dict)
                else list(frame.container)
            )
            if depth == len(self._stack) - 1 and pending is not None:
                _set_slot(copy, pending[0], pending[1])
            if child is not None and child_slot is not None:
                _set_slot(copy, child_slot, child)
            child = copy
            child_slot = frame.slot
        return child
