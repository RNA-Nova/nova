#!/usr/bin/env python3
"""模型相关交互的 PTY 全覆盖矩阵（修复验证版）。

断言全部状态感知（从屏幕读当前模型，不硬编码）。
覆盖：/model 选择器（✓/分组/未配置标记/Tab 空池守卫/搜索）、直选未配置模型、
/scoped-models 保存、ctrl+p 循环（footer 联动 + 反馈）、shift+tab thinking。
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module

smoke = import_module("pty-smoke")

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✔' if ok else '✘'} {name}" + (f" —— {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def footer_model(buffer: str):
    """从尾部 footer 行抓当前模型（provider/id · thinking 形态）。"""
    tail = buffer[-1500:]
    matches = re.findall(r"(\w[\w-]*/[\w.-]+) · \w+", tail)
    return matches[-1] if matches else None


def main() -> int:
    tui = smoke.TuiSession("/tmp")
    try:
        tui._drain(8.0)
        initial = footer_model(tui.buffer)
        print(f"  [状态] 初始模型: {initial}")

        # ---- 1. /model 选择器基本面 ----
        checkpoint = len(tui.buffer)
        tui.send("/model\r", 4.0)
        delta = tui.buffer[checkpoint:]
        check("/model 打开选择器（含当前作用域标题）", "选择模型（作用域:" in delta)
        check("当前模型 ✓ 标记", "✓" in delta)
        check("未配置凭据标记", "未配置凭据" in delta)

        # ---- 2. Tab 空池守卫（当前环境 scoped 池为空时）----
        checkpoint = len(tui.buffer)
        tui.send("\t", 2.5)
        delta = tui.buffer[checkpoint:]
        check("Tab 空池守卫提示", "Scoped 池为空" in delta)
        check("Tab 空池不落'无匹配'", "无匹配" not in delta)
        tui.send("\x1b", 2.0)

        # ---- 3. 直选未配置凭据模型 → 明确报错而非谎报 ----
        checkpoint = len(tui.buffer)
        tui.send("/model moonshotai/kimi-k2-0905-preview\r", 5.0)
        delta = tui.buffer[checkpoint:]
        check("未配置模型报'未配置凭据'", "未配置凭据" in delta)
        check("不再谎报'已切换'", "已切换模型: moonshotai" not in delta)
        check("footer 未被污染", footer_model(tui.buffer) == initial)

        # ---- 4. /scoped-models 保存两个模型 ----
        tui.send("/scoped-models\r", 4.0)
        tui.send(" ", 1.5)
        tui.send("\x1b[B", 1.0)
        tui.send(" ", 1.5)
        checkpoint = len(tui.buffer)
        tui.send("\x13", 3.0)  # ctrl+s
        delta = tui.buffer[checkpoint:]
        check("ctrl+s 保存反馈", re.search(r"已保存|Scoped", delta))

        # ---- 5. ctrl+p 循环：footer 必须真的变 ----
        before = footer_model(tui.buffer)
        checkpoint = len(tui.buffer)
        tui.send("\x10", 3.5)
        delta = tui.buffer[checkpoint:]
        after = footer_model(tui.buffer)
        print(f"  [状态] ctrl+p: {before} → {after}")
        check("ctrl+p footer 真切换", after is not None and after != before)
        check("ctrl+p 有切换通知", "已切换模型" in delta)
        tui.send("\x10", 3.0)  # 再循环一次（回弹或前进均可）
        after2 = footer_model(tui.buffer)
        check("第二次 ctrl+p 继续轮转", after2 is not None and after2 != after)

        # ---- 6. shift+tab thinking 循环（footer 档位变化）----
        tail_before = tui.buffer[-300:]
        tui.send("\x1b[Z", 3.0)
        tail_after = tui.buffer[-300:]
        check("shift+tab thinking 档位变化", tail_before != tail_after)

        # ---- 7. 搜索过滤 ----
        tui.send("/model\r", 3.0)
        checkpoint = len(tui.buffer)
        tui.send("deepseek", 2.5)
        delta = tui.buffer[checkpoint:]
        check("搜索过滤只剩 volcengine 系", "volcengine/" in delta and "moonshotai/" not in delta)
        tui.send("\x1b", 2.0)

        print()
        if FAILURES:
            print(f"FAIL {len(FAILURES)} 项:")
            for f in FAILURES:
                print("  -", f)
            return 1
        print("全部通过")
        return 0
    finally:
        tui.close()


if __name__ == "__main__":
    sys.exit(main())
