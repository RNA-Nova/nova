#!/usr/bin/env python3
"""pty-subagent：subagent 工具三模式的真实 TTY 动态呈现验证（首现时刻版）。

真实链路：主会话模型调 subagent 工具 → subagent_gate 弹窗（选"本会话
始终允许"，↓+Enter）→ 子进程逐事件 on_update → 卡片实时重绘。三场景：

1. single：一个 worker 依次写 5 个文件；
2. parallel：两个 worker 各写 5 个文件（不同目录）；
3. chain：五步链（每步写一个文件，带 {previous} 占位）。

动态性取证（重绘免疫）：加载器 spinner 每秒重绘数十次会淹没屏幕尾部，
故不数行数——改为在"自场景起点以来的全量输出"里记录每个标记的
**首次出现时刻**（重绘只会重复已出现的内容，首现时刻不受重绘影响）：
内容若实时生长，各标记的首现时刻应沿执行过程错开；若全部集中在
完成瞬间，则卡片是静态的。

同时核实落盘文件。帧与结论写 /tmp/nova-pty-subagent-frames/。

用法：python3 scripts/pty-subagent.py [--timeout 420]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module

smoke = import_module("pty-smoke")

ROOT = "/tmp/nova-pty-subagent"
FRAMES_DIR = "/tmp/nova-pty-subagent-frames"
SAMPLE_INTERVAL = 2.0

# subagent_gate 的 select 弹窗（选项：允许一次 / 本会话始终允许 / 取消）
GATE_MARK = "允许一次"
GATE_ALWAYS_KEYS = "\x1b[B\r"  # ↓ + enter = 本会话始终允许（本会话后续免问）


def run_scenario(
    tui,
    name: str,
    prompt: str,
    markers: list[str],
    done_patterns: list[str],
    timeout: float,
):
    """发 prompt → 采样循环（含门控应答）→ 命中完成模式/超时。

    返回 (first_seen, done, gate_answered, tail)：first_seen 为
    标记 → 首次出现的相对秒数（未出现缺席）。
    """
    base = len(tui.buffer)
    t0 = time.time()
    tui.send(prompt + "\r", 2.0)
    first_seen: dict[str, float] = {}
    gate_answered = False
    done = False
    pattern_hit = False
    spinner_count = 0
    spinner_last_change = 0.0

    while time.time() - t0 < timeout:
        tui._drain(SAMPLE_INTERVAL)
        slice_ = tui.buffer[base:]
        now = round(time.time() - t0, 1)
        for m in markers:
            if m not in first_seen and re.search(m, slice_):
                first_seen[m] = now

        # spinner（Working…/Running…）计数：buffer 只增不减，加载器活跃期
        # 计数持续增长；静默 4s+ 才视为轮次真结束（usage 行在流式期就可能
        # 出现——运行中占位的 exit_code=0 让渲染器提前落 usage 行）
        sc = slice_.count("Working…") + slice_.count("Running…")
        if sc != spinner_count:
            spinner_count = sc
            spinner_last_change = now

        if not gate_answered and GATE_MARK in slice_:
            tui.send(GATE_ALWAYS_KEYS, 2.0)
            gate_answered = True
            print(f"   [{name}] 门控已应答（始终允许）t={time.time()-t0:.0f}s", flush=True)
            continue

        if any(re.search(p, slice_) for p in done_patterns):
            pattern_hit = True
        if pattern_hit and now - spinner_last_change >= 4.0:
            done = True
            print(f"   [{name}] 完成（模式命中+spinner 静默）t={time.time()-t0:.0f}s", flush=True)
            break

    # 完成后再沉淀一帧（终态渲染兜底首现）
    tui._drain(3.0)
    slice_ = tui.buffer[base:]
    now = round(time.time() - t0, 1)
    for m in markers:
        if m not in first_seen and re.search(m, slice_):
            first_seen[m] = now
    return first_seen, done, gate_answered, slice_[-4000:]


def check_files(sub: str, prefix: str, count: int = 5) -> int:
    hits = 0
    for i in range(1, count + 1):
        p = os.path.join(ROOT, sub, f"{prefix}{i}.txt")
        if os.path.exists(p) and open(p).read().strip():
            hits += 1
    return hits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=420.0)
    args = parser.parse_args()

    shutil.rmtree(ROOT, ignore_errors=True)
    shutil.rmtree(FRAMES_DIR, ignore_errors=True)
    os.makedirs(ROOT)
    os.makedirs(FRAMES_DIR, exist_ok=True)

    scenarios = [
        (
            "single",
            (
                "直接调用 subagent 工具，不要自己执行：mode single，agent worker，"
                "task 内容：依次创建 5 个文件 /tmp/nova-pty-subagent/single/s1.txt "
                "到 s5.txt，内容分别是 s1 到 s5，每个文件单独一次 write 调用，"
                "不要并行，不要做其他任何事。"
            ),
            # 工具调用行（带 → write 前缀，避开输入回显里的裸路径）
            [rf"→ write [^\n]*s{i}\.txt" for i in range(1, 6)],
            [r"\d+\s+turns?"],  # usage 行（非 running 态才显示）
        ),
        (
            "parallel",
            (
                "直接调用 subagent 工具，不要自己执行：mode parallel，tasks 两个任务。"
                "任务1：agent worker，task：依次创建 5 个文件 "
                "/tmp/nova-pty-subagent/par-a/a1.txt 到 a5.txt，内容分别是 a1 到 a5，"
                "每个文件单独一次 write 调用，不要做其他事。"
                "任务2：agent worker，task：依次创建 5 个文件 "
                "/tmp/nova-pty-subagent/par-b/b1.txt 到 b5.txt，内容分别是 b1 到 b5，"
                "每个文件单独一次 write 调用，不要做其他事。"
            ),
            [
                r"0/2 done, 2 running",
                r"1/2 done, 1 running",
                r"⏳",
                r"2/2 tasks",
                r"Total:",
            ],
            [r"2/2 tasks", r"Total:"],
        ),
        (
            "chain",
            (
                "直接调用 subagent 工具，不要自己执行：mode chain，chain 五个步骤，"
                "全部 agent worker。第1步 task：创建 /tmp/nova-pty-subagent/chain/c1.txt，"
                "内容 c1，只做这一件事。第2步 task：创建 "
                "/tmp/nova-pty-subagent/chain/c2.txt，内容 c2；上一步输出：{previous}。"
                "第3步 task：创建 /tmp/nova-pty-subagent/chain/c3.txt，内容 c3；"
                "上一步输出：{previous}。第4步 task：创建 "
                "/tmp/nova-pty-subagent/chain/c4.txt，内容 c4；上一步输出：{previous}。"
                "第5步 task：创建 /tmp/nova-pty-subagent/chain/c5.txt，内容 c5；"
                "上一步输出：{previous}。"
            ),
            [rf"Step {k}:" for k in range(1, 6)] + [r"Total:"],
            [r"5/5 steps", r"Total:"],
        ),
    ]

    tui = smoke.TuiSession(ROOT)
    results = {}
    try:
        tui._drain(smoke.STARTUP_WAIT)
        tui.send("\x1b", 1.0)  # 关首次配置引导（若有）

        for name, prompt, markers, done_patterns in scenarios:
            print(f"── 场景 {name} 开始（{time.strftime('%H:%M:%S')}）", flush=True)
            first_seen, done, gate, tail = run_scenario(
                tui, name, prompt, markers, done_patterns, args.timeout
            )
            with open(os.path.join(FRAMES_DIR, f"{name}-tail.log"), "w", encoding="utf-8") as fp:
                fp.write(tail)
            results[name] = (first_seen, done, gate)
            print(
                f"   done={done} gate={gate} 首现={first_seen}",
                flush=True,
            )

        # 收尾：中止可能的残余轮次再退出
        tui.send("\x1b", 2.0)
    finally:
        tui.close()

    # ---------- 判定 ----------
    print("\n===== 判定（首现时刻表，单位秒）=====")
    failures = []

    def report(name: str) -> tuple[dict, bool, bool]:
        first_seen, done, gate = results[name]
        lines = [f"   {m!r}: 首现 t={first_seen.get(m, '未出现')}" for m in first_seen]
        print(f"{name}: done={done} gate={gate}")
        for line in sorted(
            first_seen.items(), key=lambda kv: kv[1]
        ):
            print(f"   {line[0]!r}: 首现 t={line[1]}s")
        return first_seen, done, gate

    # single：5 个 write 行首现时刻应错开（非全部挤在终点）
    fs, done, gate = report("single")
    files = check_files("single", "s")
    times = [fs.get(rf"→ write [^\n]*s{i}\.txt") for i in range(1, 6)]
    seen_times = [t for t in times if t is not None]
    spread = (max(seen_times) - min(seen_times)) if len(seen_times) >= 2 else 0
    ok_single = done and files == 5 and len(seen_times) == 5 and spread >= 3.0
    print(f"   文件={files}/5 首现跨度={spread:.1f}s → " + ("PASS" if ok_single else "FAIL"))
    if not ok_single:
        failures.append("single")

    # parallel：运行中计数器必须先于终态出现
    fs, done, gate = report("parallel")
    files_a = check_files("par-a", "a")
    files_b = check_files("par-b", "b")
    t_running = fs.get(r"0/2 done, 2 running")
    t_final = min(
        [t for m, t in fs.items() if re.search(r"2/2 tasks|Total:", m)], default=None
    )
    # 同帧并发完成是合法形态（两任务快时计数器多态挤在一个 2s 采样窗内），
    # 顺序由设计保证，故只要求 running 态不晚于终态
    ok_par = (
        done
        and files_a == 5
        and files_b == 5
        and t_running is not None
        and t_final is not None
        and t_running <= t_final
    )
    print(
        f"   文件={files_a}+{files_b}/5+5 计数器不晚于终态={t_running}<={t_final} → "
        + ("PASS" if ok_par else "FAIL")
    )
    if not ok_par:
        failures.append("parallel")

    # chain：Step 1 的首现必须显著早于 Step 5（步骤逐节生长，而非终态一把渲染）
    fs, done, gate = report("chain")
    files_c = check_files("chain", "c")
    step_times = [fs.get(rf"Step {k}:") for k in range(1, 6)]
    seen_steps = [t for t in step_times if t is not None]
    step_spread = (max(seen_steps) - min(seen_steps)) if len(seen_steps) >= 2 else 0
    ok_chain = done and files_c == 5 and len(seen_steps) == 5 and step_spread >= 3.0
    print(
        f"   文件={files_c}/5 Step 首现跨度={step_spread:.1f}s → "
        + ("PASS" if ok_chain else "FAIL")
    )
    if not ok_chain:
        failures.append("chain")

    print(f"\n帧目录：{FRAMES_DIR}")
    if failures:
        print(f"FAIL: {failures}")
        return 1
    print("PASS: subagent 三模式动态呈现全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
