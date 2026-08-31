"""SSH 远程 executor 供给器（nova_coding_agent bundle）。

``/executor remote <user@host>`` 的远程实例供给（类 VS Code Remote-SSH 模型）：

- **身份层即 SSH**：远程 executor 只听 127.0.0.1，传输与身份全部走 SSH
  通道（本地回环隧道转发），无公网暴露面；
- **密钥优先，密码只做首连引导**：BatchMode 探测先行；失败后经
  ``bootstrap`` 回调（TUI 终端让位，复用 ``dialog:interactive-shell``）让
  用户对着原生 ssh 提示符输一次密码，同时把 Nova 管理密钥
  （``~/.nova/agent/executor/id_ed25519``）装入远端 authorized_keys——
  之后永远免密；密码本身不进 Nova 进程、不落盘；
- **token 一次性**：每次供给现生成（``secrets.token_hex``），经 ssh 命令行
  下发 ``--auth-token``，不写任何文件；
- **二进制按平台缓存上传**：``uname -sm`` 探测平台 → 本地缓存
  ``~/.nova/agent/executor/bin/<platform>/nova-executor`` → scp 上传至远端
  ``~/.nova/agent/executor/bin/nova-executor``（tmp + mv 原子替换）；
- **单 ssh 进程承载隧道与远程进程**：``ssh -tt -L lport:127.0.0.1:rport
  target 'exec nova-executor ...'``——exec 使 executor 成为远程 PTY 会话
  leader：连接一断（terminate / 断网 / 断电），sshd 即发 SIGHUP 回收，
  远程永不留孤儿（stdin 看门狗方案已实证与 executor 启动冲突，弃用）。
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import secrets
import shlex
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from nova_harness.config.defaults import get_agent_dir

SSH_SCHEME = "ssh://"

# 远程 executor 托管路径（远端家目录下——远程只听回环，token 随行下发）
REMOTE_BIN = "~/.nova/agent/executor/bin/nova-executor"
_REMOTE_BIN_DIR = "~/.nova/agent/executor/bin"

_PROBE_TIMEOUT_S = 20.0
_CMD_TIMEOUT_S = 30.0
_SPAWN_READY_TIMEOUT_S = 15.0
# scp 上传超时（二进制 ~20MB，慢链路放宽）
_UPLOAD_TIMEOUT_S = 300.0

# 远程监听端口候选区间（避开知名端口；冲突时换端口重试）
_REMOTE_PORT_MIN = 20000
_REMOTE_PORT_MAX = 45000
_SPAWN_PORT_ATTEMPTS = 3

# 供给进度回调：on_progress(step_text)——扩展层映射为 footer 状态/notice
ProgressFn = Callable[[str], None]
# 首连引导回调：bootstrap(interactive_command) -> exit_code（0 = 成功）
BootstrapFn = Callable[[str], Awaitable[int]]


class ProvisionError(RuntimeError):
    """供给失败（带步骤标签——/executor 的错误回执按步给修复指引）。"""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step


@dataclass(frozen=True)
class SshTarget:
    """解析后的 SSH 目标（``[user@]host[:port]``）。"""

    host: str
    user: Optional[str] = None
    port: Optional[int] = None

    @property
    def ssh_dest(self) -> str:
        """ssh/scp 的目的地参数（user@host 或 host）。"""
        return f"{self.user}@{self.host}" if self.user else self.host

    @property
    def display(self) -> str:
        text = self.ssh_dest
        if self.port is not None:
            text += f":{self.port}"
        return text

    @property
    def default_name(self) -> str:
        """自动登记时的缺省端点名（主机名）。"""
        return self.host

    @property
    def canonical_url(self) -> str:
        """规范化 ssh:// URL（BackendSelection.url / 会话条目 / settings 端点用）。"""
        return f"{SSH_SCHEME}{self.display}"


def is_ssh_url(url: str) -> bool:
    """该 url 是否 SSH 目标（``ssh://`` 方案）。"""
    return url.startswith(SSH_SCHEME)


def parse_ssh_target(text: str) -> SshTarget:
    """解析 ``ssh://[user@]host[:port]`` 或裸 ``[user@]host[:port]``。

    失败抛 ``ProvisionError("parse", ...)``——裸字符串解析不出的才是错误；
    未知名称按 SSH 目标处理是 /executor 的有意语义（ssh config 别名同享）。
    """
    raw = text.strip()
    if raw.startswith(SSH_SCHEME):
        raw = raw[len(SSH_SCHEME) :]
    if not raw or any(ch.isspace() for ch in raw):
        raise ProvisionError(
            "parse", f"无效的 SSH 目标：{text!r}（应为 [user@]host[:port]）"
        )
    user: Optional[str] = None
    port: Optional[int] = None
    host = raw
    if "@" in host:
        user, host = host.rsplit("@", 1)
        if not user:
            raise ProvisionError("parse", f"无效的 SSH 目标：{text!r}（user 为空）")
    if ":" in host:
        host, _, port_text = host.rpartition(":")
        if not port_text.isdigit():
            raise ProvisionError("parse", f"无效的 SSH 目标：{text!r}（端口非数字）")
        port = int(port_text)
        if not (1 <= port <= 65535):
            raise ProvisionError("parse", f"无效的 SSH 目标：{text!r}（端口越界）")
    if not host:
        raise ProvisionError("parse", f"无效的 SSH 目标：{text!r}（host 为空）")
    return SshTarget(host=host, user=user, port=port)


# ---------------------------------------------------------------------------
# Nova 管理密钥（首连引导后的免密件）
# ---------------------------------------------------------------------------


def _executor_state_dir() -> Path:
    path = Path(get_agent_dir()) / "executor"
    path.mkdir(parents=True, exist_ok=True)
    return path


def managed_key_pair() -> tuple[Path, Path]:
    """Nova 管理密钥路径（私钥 / 公钥）。"""
    state = _executor_state_dir()
    return state / "id_ed25519", state / "id_ed25519.pub"


def ensure_managed_key() -> Path:
    """确保 Nova 管理密钥存在（缺则 ssh-keygen 生成，空口令——仅供 ssh 认证用）。"""
    priv, pub = managed_key_pair()
    if priv.is_file() and pub.is_file():
        return priv
    proc = subprocess.run(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "nova-executor",
            "-f",
            str(priv),
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0 or not priv.is_file():
        raise ProvisionError(
            "key", f"生成 Nova 管理密钥失败：{proc.stderr.strip() or proc.returncode}"
        )
    os.chmod(priv, 0o600)
    return priv


# ---------------------------------------------------------------------------
# ssh/scp 命令构造
# ---------------------------------------------------------------------------


def _ssh_options(target: SshTarget, *, port_flag: str = "-p") -> list[str]:
    """ssh/scp 共用选项（BatchMode 非交互 + TOFU 收 host key + 保活）。

    管理密钥存在即以 ``-i`` 加入（与默认密钥链/ssh-agent 是叠加关系，
    非 IdentitiesOnly）；密码提示在 BatchMode 下被禁用——交互路径归
    ``bootstrap_command``（终端让位）。
    """
    options = [
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=4",
        "-o",
        "ConnectTimeout=10",
    ]
    priv, _pub = managed_key_pair()
    if priv.is_file():
        options += ["-i", str(priv)]
    if target.port is not None:
        options += [port_flag, str(target.port)]
    return options


async def _exec(*args: str, stdin: Optional[int] = None) -> asyncio.subprocess.Process:
    """创建子进程（独立一层便于测试替换）。

    spawn 路径 stdin 显式 PIPE（持有、永不写入）——避免继承 TTY 时
    远程 PTY 回显/抢读终端输入；生命周期回收不依赖 stdin（归 PTY +
    exec 的 SIGHUP 路径）。
    """
    return await asyncio.create_subprocess_exec(
        *args,
        stdin=stdin,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _run(args: list[str], timeout: float) -> tuple[int, str, str]:
    """跑一个短命令，返回 (exit_code, stdout, stderr)。"""
    proc = await _exec(*args)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ProvisionError("ssh", f"命令超时（{' '.join(args[:2])}，{timeout:.0f}s）")
    return (
        proc.returncode if proc.returncode is not None else -1,
        out.decode("utf-8", errors="replace"),
        err.decode("utf-8", errors="replace"),
    )


# ---------------------------------------------------------------------------
# 探测 / 首连引导 / 二进制上传
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    """远程探测结果。"""

    uname: str  # `uname -sm` 输出（如 "Linux x86_64"）
    remote_bin_ok: bool  # 远程托管二进制已存在且可执行
    home: str = ""  # 远程家目录（`pwd`——remote_cwd 缺省值）
    shell: str = ""  # 远程登录 shell 基名（`$SHELL`——环境段呈现用）
    rg_path: str = ""  # 远程 rg 路径（`command -v rg`——grep 远程加速白嫖件）

    @property
    def platform_key(self) -> str:
        return platform_cache_key(self.uname)


# 探测脚本：平台 + 家目录 + 登录 shell + rg 可用性 + 托管二进制存在性
# （一次 ssh 拿全）
_PROBE_SCRIPT = (
    "uname -sm; pwd; echo ${SHELL:-sh}; command -v rg || true; "
    f"if test -x {REMOTE_BIN}; then echo __BIN_OK__; else echo __BIN_MISSING__; fi"
)


async def probe(target: SshTarget) -> ProbeResult:
    """BatchMode 探测远程平台/家目录/shell/rg 与 executor 二进制状态。

    失败抛 ProvisionError——stderr 含 Permission denied 记 ``auth`` 步
    （可首连引导），其余记 ``connect`` 步（网络/主机问题，引导无意义）。
    """
    args = ["ssh", *_ssh_options(target), target.ssh_dest, _PROBE_SCRIPT]
    code, out, err = await _run(args, _PROBE_TIMEOUT_S)
    if code != 0:
        detail = err.strip().splitlines()[-1] if err.strip() else f"exit {code}"
        step = "auth" if "Permission denied" in err else "connect"
        raise ProvisionError(step, f"SSH 连接失败（{target.display}）：{detail}")
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    info = [line for line in lines if not line.startswith("__BIN_")]
    if not info:
        raise ProvisionError(
            "connect", f"远程探测输出异常（{target.display}）：{out!r}"
        )
    return ProbeResult(
        uname=info[0],
        home=info[1] if len(info) > 1 else "",
        shell=os.path.basename(info[2]) if len(info) > 2 else "",
        rg_path=info[3] if len(info) > 3 else "",
        remote_bin_ok="__BIN_OK__" in lines,
    )


_OS_NAMES = {"linux": "linux", "darwin": "macos"}
_ARCH_NAMES = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "aarch64": "arm64",
    "arm64": "arm64",
}


def platform_cache_key(uname_sm: str) -> str:
    """``uname -sm`` 输出 → 平台缓存键（如 "Linux x86_64" → "linux-x86_64"）。"""
    parts = uname_sm.strip().split()
    sys_name = _OS_NAMES.get(parts[0].lower(), parts[0].lower()) if parts else "unknown"
    arch = (
        _ARCH_NAMES.get(parts[-1].lower(), parts[-1].lower())
        if len(parts) > 1
        else "unknown"
    )
    return f"{sys_name}-{arch}"


def local_cache_binary(platform_key: str) -> Optional[Path]:
    """本地缓存中该平台的 executor 二进制（无则 None）。"""
    candidate = _executor_state_dir() / "bin" / platform_key / "nova-executor"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    return None


def bootstrap_command(target: SshTarget) -> str:
    """首连引导命令（交互式——经终端让位在真实 TTY 执行，用户输一次密码）。

    把 Nova 管理公钥幂等装入远端 authorized_keys；之后 BatchMode 永远免密。
    命令在本地 shell 执行（dialog:interactive-shell 语义），故用 ``<`` 重定向
    喂公钥；密码提示走原生 ssh——密码本身不经过 Nova 进程。
    注意不带 BatchMode（密码提示必须可用），限一次密码尝试防反复卡住。
    """
    _priv, pub = managed_key_pair()
    options = [
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "NumberOfPasswordPrompts=1",
        "-o",
        "ConnectTimeout=15",
    ]
    if target.port is not None:
        options += ["-p", str(target.port)]
    remote = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        "touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && "
        'key="$(cat)" && '
        '(grep -qxF "$key" ~/.ssh/authorized_keys || echo "$key" >> ~/.ssh/authorized_keys) && '
        "echo __NOVA_KEY_INSTALLED__"
    )
    return f"ssh {' '.join(options)} {target.ssh_dest} {shlex.quote(remote)} < {shlex.quote(str(pub))}"


async def ensure_remote_binary(
    target: SshTarget,
    probe_result: ProbeResult,
    on_progress: Optional[ProgressFn] = None,
) -> None:
    """确保远程托管二进制就绪（缺则从本地平台缓存 scp 上传，tmp+mv 原子替换）。"""
    if probe_result.remote_bin_ok:
        return
    platform = probe_result.platform_key
    local = local_cache_binary(platform)
    if local is None:
        cache_dir = _executor_state_dir() / "bin" / platform
        raise ProvisionError(
            "binary",
            f"远程平台 {platform} 无缓存二进制——请在对应平台构建后放入 "
            f"{cache_dir}/nova-executor（如 cargo build --release && "
            f"cp target/release/nova-executor {cache_dir}/）",
        )
    size_mb = local.stat().st_size / 1024 / 1024
    if on_progress is not None:
        on_progress(f"上传 executor 二进制（{platform} · {size_mb:.1f}MB）…")
    tmp_remote = f"{_REMOTE_BIN_DIR}/.nova-executor.upload"
    code, _out, err = await _run(
        ["ssh", *_ssh_options(target), target.ssh_dest, f"mkdir -p {_REMOTE_BIN_DIR}"],
        _CMD_TIMEOUT_S,
    )
    if code != 0:
        raise ProvisionError("binary", f"创建远程目录失败：{err.strip()}")
    code, _out, err = await _run(
        [
            "scp",
            "-q",
            *_ssh_options(target, port_flag="-P"),
            str(local),
            f"{target.ssh_dest}:{tmp_remote}",
        ],
        _UPLOAD_TIMEOUT_S,
    )
    if code != 0:
        raise ProvisionError("binary", f"上传 executor 二进制失败：{err.strip()}")
    code, _out, err = await _run(
        [
            "ssh",
            *_ssh_options(target),
            target.ssh_dest,
            f"chmod 755 {tmp_remote} && mv {tmp_remote} {REMOTE_BIN}",
        ],
        _CMD_TIMEOUT_S,
    )
    if code != 0:
        raise ProvisionError("binary", f"安装远程二进制失败：{err.strip()}")


# ---------------------------------------------------------------------------
# 远程启动 + 回环隧道
# ---------------------------------------------------------------------------


def _free_local_port() -> int:
    """取一个本地空闲回环端口（随即释放——有小竞争窗，配合重试兜底）。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class SshRemoteHandle:
    """一个活着的远程 executor（本地 ssh 进程承载隧道 + 远程进程）。"""

    target: SshTarget
    url: str  # 本地隧道入口（ws://127.0.0.1:<lport>）
    token: str
    process: Any  # asyncio.subprocess.Process
    platform: str = ""
    default_cwd: str = ""  # 远程家目录（remote_cwd 缺省值，探测所得）
    remote_shell: str = ""  # 远程登录 shell 基名（环境段呈现用）
    rg_path: str = ""  # 远程 rg 路径（grep 远程加速白嫖件，探测所得）

    def alive(self) -> bool:
        return self.process.returncode is None

    def stop(self) -> None:
        """终止本地 ssh——隧道断开，远程 executor（PTY 会话 leader）收
        SIGHUP 随即退出。"""
        if self.process.returncode is None:
            try:
                self.process.terminate()
            except (ProcessLookupError, OSError):
                pass


_LISTEN_RE = re.compile(rb"ws://\S+:(\d+)")


async def _wait_listen(proc: asyncio.subprocess.Process) -> None:
    """从 ssh stdout 等远程 executor 的监听行（提前退出则带输出尾行抛错）。

    -tt 下远程 stderr 并入 PTY stdout——错误文本也在扫描流里，故维护
    尾行缓冲做失败诊断（stderr 恒空）。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _SPAWN_READY_TIMEOUT_S
    assert proc.stdout is not None
    tail: list[str] = []

    def _detail(fallback: str) -> str:
        return tail[-1] if tail else fallback

    while loop.time() < deadline:
        if proc.returncode is not None:
            raise RuntimeError(_detail(f"exit {proc.returncode}"))
        try:
            line = await asyncio.wait_for(
                proc.stdout.readline(),
                timeout=max(0.1, deadline - loop.time()),
            )
        except asyncio.TimeoutError:
            break
        if not line:  # EOF——ssh 已退出
            raise RuntimeError(_detail("ssh 提前退出"))
        if _LISTEN_RE.search(line):
            return
        text = line.decode("utf-8", errors="replace").strip()
        if text:
            tail.append(text)
            tail = tail[-5:]
    raise RuntimeError(_detail("启动超时（未打印监听地址）"))


async def spawn_remote(
    target: SshTarget,
    probe_result: Optional[ProbeResult] = None,
) -> SshRemoteHandle:
    """单 ssh 进程启动远程 executor + 回环隧道（端口冲突换端口重试）。

    生命周期靠 **PTY + exec**（-tt 强制远程 PTY，stdin 非 TTY 也生效）：
    ``exec`` 使 executor 成为远程会话 leader，连接一断（terminate / 断网
    / 本机断电）sshd 即向它发 SIGHUP——远程永不留孤儿。
    注意不能用 stdin 看门狗（``cat`` 类）：实证远程 executor 与任何
    读取 channel stdin 的进程共存时启动即卡死。
    PTY 副作用：stdout 行经 onlcr（\\r\\n——监听行 regex 不受影响）、
    stderr 并入 stdout（诊断归尾行缓冲，见 ``_wait_listen``）；远程命令
    前缀 ``TERM=dumb`` 抑制交互式日志变体。
    """
    last_error = "未知错误"
    for _attempt in range(_SPAWN_PORT_ATTEMPTS):
        token = secrets.token_hex(16)
        rport = random.randint(_REMOTE_PORT_MIN, _REMOTE_PORT_MAX)
        lport = _free_local_port()
        remote_cmd = (
            f"TERM=dumb exec {REMOTE_BIN} --listen ws://127.0.0.1:{rport} "
            f"--auth bearer --auth-token {token}"
        )
        args = [
            "ssh",
            *_ssh_options(target),
            "-tt",
            "-o",
            "ExitOnForwardFailure=yes",
            "-L",
            f"127.0.0.1:{lport}:127.0.0.1:{rport}",
            target.ssh_dest,
            remote_cmd,
        ]
        proc = await _exec(*args, stdin=asyncio.subprocess.PIPE)
        try:
            await _wait_listen(proc)
            return SshRemoteHandle(
                target=target,
                url=f"ws://127.0.0.1:{lport}",
                token=token,
                process=proc,
                platform=probe_result.platform_key if probe_result else "",
                default_cwd=probe_result.home if probe_result else "",
                remote_shell=probe_result.shell if probe_result else "",
                rg_path=probe_result.rg_path if probe_result else "",
            )
        except Exception as exc:  # 聚合为最后一次错误后重试
            last_error = str(exc)
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
    raise ProvisionError(
        "spawn", f"远程 executor 启动失败（{target.display}）：{last_error}"
    )


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------


async def provision(
    target: SshTarget,
    on_progress: Optional[ProgressFn] = None,
    bootstrap: Optional[BootstrapFn] = None,
) -> SshRemoteHandle:
    """供给一个远程 executor 并就绪（密钥 → 探测 → 二进制 → 隧道）。

    - BatchMode 探测 auth 失败且给了 ``bootstrap`` → 首连引导（终端让位输
      一次密码、装入管理密钥）后重探；
    - 无 ``bootstrap``（headless / 执行期懒路径）→ 直接报错并附
      ssh-copy-id 指引；
    - connect 类失败（主机不可达）不引导——密码救不了网络。
    """
    progress = on_progress or (lambda _text: None)

    progress("检查 Nova 管理密钥…")
    ensure_managed_key()

    progress(f"探测 {target.display}…")
    try:
        probe_result = await probe(target)
    except ProvisionError as exc:
        if exc.step != "auth":
            raise
        if bootstrap is None:
            raise ProvisionError(
                "auth",
                f"SSH 免密登录失败（{target.display}）——请先 ssh-copy-id "
                f"{target.display}，或在 TUI 中经 /executor 首连引导",
            ) from exc
        progress("首次连接：请在终端输入密码完成密钥登记…")
        code = await bootstrap(bootstrap_command(target))
        if code != 0:
            raise ProvisionError(
                "auth",
                f"首次连接未完成（exit {code}）——密码错误、连接失败或已取消",
            ) from exc
        probe_result = await probe(target)

    progress(f"平台 {probe_result.platform_key} · 检查远程二进制…")
    await ensure_remote_binary(target, probe_result, progress)

    progress("启动远程 executor 并建立隧道…")
    handle = await spawn_remote(target, probe_result)
    progress(f"就绪（{handle.url}）")
    return handle
