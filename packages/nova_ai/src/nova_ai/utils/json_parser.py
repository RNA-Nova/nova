"""
JSON解析工具
用于解析流式响应中的部分JSON
"""

import json
from typing import Any, Dict, List, Optional, Union

from json_repair import repair_json


def parse_streaming_json(json_str: Optional[str]) -> Union[Dict[str, Any], List[Any]]:
    """
    解析流式响应中的部分JSON

    始终返回一个有效的对象，即使JSON不完整。

    Args:
        json_str: 流式响应中的部分JSON字符串

    Returns:
        解析后的对象，如果解析失败则返回空对象
    """
    if not json_str or json_str.strip() == "":
        return {}

    # 首先尝试标准解析（对于完整JSON最快）
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # 使用 json_repair 修复并解析
    try:
        repaired = repair_json(json_str, skip_json_loads=True)
        if repaired:
            return json.loads(repaired)
    except Exception:
        pass

    # 如果所有解析都失败，返回空对象
    return {}


class StreamingJsonParser:
    """增量流式 JSON 解析器（工具参数 delta 累积专用）。

    逐 delta 全量 ``json_repair`` 是 O(n²)：14KB 参数 × 71 个 delta 实测
    3.7s 纯解析开销，且全发生在事件循环热路径上。本类把成本压回 O(n)：

    - 每个 delta 只对**新增片段**做 O(delta) 扫描，维护字符串/转义/括号栈
      三项状态（纯 Python，成本与 delta 长度同阶）；
    - 截断前缀用最小闭合序列（补引号/反括号）补全后交 C 级 ``json.loads``
      ——完整 JSON 直接精确命中，截断 JSON 得到逐 delta 的部分快照；
    - 闭合失败（``{"a":`` 这类悬空 token 窗口）保留上一快照，不回退到
      纯 Python 修复；``json_repair`` 只在 ``finish()`` 兜底一次，
      终值与 ``parse_streaming_json`` 完全一致。
    """

    def __init__(self) -> None:
        self._text = ""
        self._value: Any = {}
        # 扫描状态：栈记录未闭合的 { 与 [
        self._stack: List[str] = []
        self._in_string = False
        self._escape = False

    @property
    def value(self) -> Any:
        """当前最新解析结果（token 间隙窗口可能是上一快照，始终为合法对象）。"""
        return self._value

    def feed(self, delta: str) -> None:
        """追加一段 delta 并刷新解析快照。"""
        self._text += delta
        self._scan(delta)
        try:
            self._value = json.loads(self._text)
            return
        except ValueError:
            pass
        closed = self._text + self._closers()
        try:
            self._value = json.loads(closed)
        except ValueError:
            # 悬空 token（如 ``{"a": ``）或字面量中途（``tru``）：保留上一快照
            pass

    def finish(self) -> Any:
        """终态精确解析（块收尾时调用）。终值语义与 parse_streaming_json 一致。"""
        try:
            self._value = json.loads(self._text)
        except ValueError:
            self._value = parse_streaming_json(self._text)
        return self._value

    def _scan(self, delta: str) -> None:
        """对新增片段推进扫描状态。"""
        self._scan_impl(delta)

    def _scan_impl(self, chunk: str) -> None:
        in_string = self._in_string
        escape = self._escape
        stack = self._stack
        for char in chunk:
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char in "{[":
                stack.append(char)
            elif char in "}]":
                if stack:
                    stack.pop()
        self._in_string = in_string
        self._escape = escape

    def _closers(self) -> str:
        """截断前缀的最小闭合序列（纯函数——不得改写扫描状态，
        下一 delta 仍从文本真实位置继续扫）。"""
        closers = []
        if self._escape:
            # 悬空反斜杠：先补 \ 使其成为合法转义，再闭合字符串
            closers.append("\\")
        if self._in_string:
            closers.append('"')
        closers.extend(
            "}" if bracket == "{" else "]" for bracket in reversed(self._stack)
        )
        return "".join(closers)
