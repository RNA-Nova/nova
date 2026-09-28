"""执行端点注册表（批次 D——词汇搬家：settings ``executor.endpoints`` →
``~/.nova/exec-server/config.toml`` 的 ``[[environments]]``）。

读写纪律：

- **读**走 SDK 层栈 loader（``load_executor_config``——配置发现/解析/
  校验全在 SDK）；
- **写**做 TOML 文本手术——只动 ``[[environments]]`` 段（增删按 id 匹配
  的条目块），其余段（sandbox_mode/[otel]/[network_proxy]/
  default_environment）原样保留；咨询式文件锁对位规则写门。

注册即环境（codex 词表）：ws(s) 端点登记为 `url` 环境；SSH 目标登记为
`program = "ssh"` 环境（对位 codex environments.toml 的 ssh 承载形态——
磁盘上只有合法词汇，SDK 校验直通）。读侧把 ssh program 条目还原为
bundle 的 canonical `ssh://` URL（现有 /executor 流程按 URL scheme 路由
ssh 供给，零改动）。
"""

from __future__ import annotations

import re
from pathlib import Path

from filelock import FileLock
from nova_exec_server_client import default_exec_server_home

_ENVIRONMENTS_HEADER = "[[environments]]"


def endpoints_config_path() -> Path:
    """端点注册表所在 config.toml 路径。"""
    return Path(default_exec_server_home()) / "config.toml"


def load_endpoints() -> list[dict]:
    """读全部已登记端点（SDK loader——坏文件响亮抛错）。

    ssh program 条目还原为 canonical `ssh://` URL（bundle 路由词汇）。
    """
    from nova_exec_server_client import load_executor_config

    config = load_executor_config(project_trusted=False)
    out = []
    for e in config.environments:
        if e.url:
            out.append({"name": e.id, "url": e.url, "cwd": e.cwd})
        elif e.program == "ssh" and e.args:
            out.append({"name": e.id, "url": f"ssh://{e.args[0]}", "cwd": e.cwd})
    return out


def register_endpoint(name: str, url: str, cwd: str | None = None) -> None:
    """登记/覆盖端点（[[environments]] 条目；同名替换）。"""
    path = endpoints_config_path()
    lock = FileLock(str(path) + ".lock")
    with lock:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        _removed, text = _remove_environment_block(text, name)
        entry_lines = [f"{_ENVIRONMENTS_HEADER}", f'id = "{name}"']
        if url.startswith("ssh://"):
            # SSH 目标按 codex 词表登记为 ssh program 环境
            entry_lines.append('program = "ssh"')
            entry_lines.append(f'args = ["{url[len("ssh://") :]}"]')
        else:
            entry_lines.append(f'url = "{url}"')
        if cwd:
            entry_lines.append(f'cwd = "{cwd}"')
        addition = "\n".join(entry_lines) + "\n"
        if text and not text.endswith("\n"):
            text += "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + addition, encoding="utf-8")


def unregister_endpoint(name: str) -> bool:
    """移除端点；返回是否真的移除过。"""
    path = endpoints_config_path()
    if not path.exists():
        return False
    lock = FileLock(str(path) + ".lock")
    with lock:
        text = path.read_text(encoding="utf-8")
        removed, new_text = _remove_environment_block(text, name)
        if removed:
            path.write_text(new_text, encoding="utf-8")
        return removed


def _remove_environment_block(text: str, name: str) -> tuple[bool, str]:
    """从 TOML 文本摘除指定 id 的 [[environments]] 条目块。

    块边界：行首 ``[[environments]]`` 起，到下一个行首 ``[`` 头或 EOF。
    返回（是否摘除，新文本）。
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    removed = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() == _ENVIRONMENTS_HEADER:
            # 收集整块
            j = i + 1
            while j < len(lines) and not lines[j].lstrip().startswith("["):
                j += 1
            block = lines[i:j]
            if any(re.match(rf'\s*id\s*=\s*"{re.escape(name)}"\s*$', b) for b in block):
                removed = True
                i = j
                continue
            out.extend(block)
            i = j
            continue
        out.append(line)
        i += 1
    return removed, "".join(out)
