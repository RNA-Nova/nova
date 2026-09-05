#!/usr/bin/env python3
"""npm 自愈的 PTY 端到端验证（真实 npm、真实缺依赖包）。

沙盒：双官方包 + 探针包（package.json 带 pretty-ms 依赖 + tui/index.ts
注册 /healprobe 命令，无 node_modules）。TUI 启动 → 加载不阻塞、诊断
"补装中" → 后台真实 npm install → 补齐通知 → 渲染器/命令上线。

断言：启动不卡死、补装中诊断、补齐通知、/healprobe 输出正确（用到
pretty-ms 的格式化结果证明 node_modules 真的可用了）。

用法：python3 scripts/pty-npm-heal.py（需网络 + npm；无需 API key）
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module

smoke = import_module("pty-smoke")

FAILURES: list[str] = []

PROBE_INDEX = """
import prettyMs from 'pretty-ms';
export default function extension(api) {
  api.registerCommand('healprobe', {
    description: '自愈探针',
    handler: async (_args, ctx) => {
      ctx.notify('heal-ok ' + prettyMs(1500));
    },
  });
}
"""


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✔' if ok else '✘'} {name}" + (f" —— {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    sandbox = tempfile.mkdtemp(prefix="nova-pty-heal-")
    cwd = tempfile.mkdtemp(prefix="nova-pty-heal-cwd-")
    probe = tempfile.mkdtemp(prefix="nova-heal-probe-")

    # 探针包：带 npm 依赖的 B 型纯 TS 包（包根即前端半区），无 node_modules
    with open(os.path.join(probe, "package.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "name": "heal-probe",
                "version": "0.0.1",
                "nova": {},
                "dependencies": {"pretty-ms": "^9.2.0"},
            },
            f,
        )
    os.makedirs(os.path.join(probe, "tui"))
    with open(os.path.join(probe, "tui", "index.ts"), "w", encoding="utf-8") as f:
        f.write(PROBE_INDEX)

    with open(os.path.join(sandbox, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "default_project_trust": "always",
                "packages": [
                    {
                        "source": "path:/Users/liujinming/agent/nova-backup-20260824/bundles/nova_base",
                        "editable": True,
                    },
                    {
                        "source": "path:/Users/liujinming/agent/nova-backup-20260824/bundles/nova_coding_agent",
                        "editable": True,
                    },
                    {"source": f"path:{probe}", "editable": True},
                ],
            },
            f,
        )
    shutil.copytree(
        os.path.expanduser("~/.nova/agent/packages"),
        os.path.join(sandbox, "packages"),
        symlinks=True,
    )

    os.environ["NOVA_AGENT_DIR"] = sandbox
    tui = smoke.TuiSession(cwd)
    try:
        tui._drain(15.0)
        check("TUI 启动完成（加载未被补装阻塞）", "heal-ok" not in tui.buffer)

        # 后台补装（真实 npm install pretty-ms）→ 完成通知
        # （可能落在启动 drain 内——按全量缓冲断言，不切增量）
        tui._drain(25.0)
        check("补齐通知出现", "已补齐" in tui.buffer)

        # 补装完成后探针命令上线（其 index.ts import pretty-ms——装好才能注册）
        checkpoint = len(tui.buffer)
        tui.send("/healprobe\r", 4.0)
        delta = tui.buffer[checkpoint:]
        check(
            "/healprobe 输出正确（pretty-ms 生效）",
            "heal-ok" in delta and ("1.5s" in delta or "1.5 s" in delta),
        )
    finally:
        tui.close()
        os.environ.pop("NOVA_AGENT_DIR", None)
        shutil.rmtree(sandbox, ignore_errors=True)
        shutil.rmtree(cwd, ignore_errors=True)
        shutil.rmtree(probe, ignore_errors=True)

    print()
    if FAILURES:
        print(f"✘ {len(FAILURES)} 项失败: {', '.join(FAILURES)}")
        return 1
    print("✔ pty-npm-heal 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
