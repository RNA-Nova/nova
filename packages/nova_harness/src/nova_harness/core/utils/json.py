"""JSON 工具：剥离注释（对齐 TS ``utils/json.ts`` 的 ``stripJsonComments``）。

支持 ``//`` 行注释与 ``/* */`` 块注释，字符串字面量内的注释标记不受影响。
"""

from __future__ import annotations


def strip_json_comments(content: str) -> str:
    """移除 JSON 文本中的注释，保留字符串内容原样。"""
    result: list[str] = []
    i = 0
    n = len(content)
    in_string = False

    while i < n:
        ch = content[i]

        if in_string:
            result.append(ch)
            if ch == "\\" and i + 1 < n:
                result.append(content[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            result.append(ch)
            i += 1
            continue

        if ch == "/" and i + 1 < n:
            nxt = content[i + 1]
            if nxt == "/":
                # 行注释：跳到行尾（保留换行，维持行号）
                end = content.find("\n", i)
                if end < 0:
                    break
                result.append("\n")
                i = end + 1
                continue
            if nxt == "*":
                # 块注释：跳过闭合，保留其中的换行
                end = content.find("*/", i + 2)
                if end < 0:
                    break
                segment = content[i : end + 2]
                result.extend("\n" for c in segment if c == "\n")
                i = end + 2
                continue

        result.append(ch)
        i += 1

    return "".join(result)


__all__ = ["strip_json_comments"]
