"""SSH 远程供给器测试（provision.py）。

子进程经 ``provision._exec`` 替换为假实现——不触真实 ssh/scp；
编排逻辑（probe/binary/spawn 链与 bootstrap 分支）经 monkeypatch 模块级
函数隔离验证。
"""

import asyncio
from pathlib import Path

import pytest

from nova_coding_agent.executor import provision


class _FakeStream:
    """假 stdout/stderr 流（readline 逐行吐，read 一次性吐剩余）。"""

    def __init__(self, lines=()):
        self._lines = list(lines)

    async def readline(self):
        await asyncio.sleep(0)
        return self._lines.pop(0) if self._lines else b""

    async def read(self):
        data = b"".join(self._lines)
        self._lines.clear()
        return data


class _FakeProcess:
    """假 asyncio 子进程：communicate 给固定输出；流式字段按需给。"""

    def __init__(self, stdout=b"", stderr=b"", code=0, stream_lines=None):
        self._stdout_data = stdout
        self._stderr_data = stderr
        self._code = code
        self.returncode = None
        self.stdout = _FakeStream(stream_lines if stream_lines is not None else [])
        self.stderr = _FakeStream([stderr] if stderr else [])
        self.killed = False
        self.terminated = False

    async def communicate(self):
        self.returncode = self._code
        return self._stdout_data, self._stderr_data

    def kill(self):
        self.killed = True
        self.returncode = -9

    def terminate(self):
        if self.returncode is None:
            self.returncode = -15
            self.terminated = True

    async def wait(self):
        if self.returncode is None:
            self.returncode = self._code
        return self.returncode


def _patch_exec(monkeypatch, procs):
    """让 provision._exec 依次返回给定假进程，并记录调用参数。"""
    calls = []

    async def _fake_exec(*args, **kwargs):
        calls.append(list(args))
        return procs.pop(0)

    monkeypatch.setattr(provision, "_exec", _fake_exec)
    return calls


# ---------------------------------------------------------------------------
# parse_ssh_target
# ---------------------------------------------------------------------------


class TestParseSshTarget:
    def test_user_host(self):
        target = provision.parse_ssh_target("alice@gpu-01")
        assert target.user == "alice"
        assert target.host == "gpu-01"
        assert target.port is None
        assert target.ssh_dest == "alice@gpu-01"
        assert target.canonical_url == "ssh://alice@gpu-01"
        assert target.default_name == "gpu-01"

    def test_host_only(self):
        target = provision.parse_ssh_target("gpu-01")
        assert target.user is None
        assert target.ssh_dest == "gpu-01"

    def test_ssh_scheme_with_port(self):
        target = provision.parse_ssh_target("ssh://alice@10.0.0.2:2222")
        assert (target.user, target.host, target.port) == ("alice", "10.0.0.2", 2222)
        assert target.display == "alice@10.0.0.2:2222"
        assert target.canonical_url == "ssh://alice@10.0.0.2:2222"

    def test_bare_host_with_port(self):
        target = provision.parse_ssh_target("gpu-01:2222")
        assert (target.host, target.port) == ("gpu-01", 2222)

    @pytest.mark.parametrize(
        "bad", ["", "   ", "@host", "user@", "host:abc", "host:0", "a b", "ssh://"]
    )
    def test_invalid(self, bad):
        with pytest.raises(provision.ProvisionError) as excinfo:
            provision.parse_ssh_target(bad)
        assert excinfo.value.step == "parse"


class TestPlatformCacheKey:
    @pytest.mark.parametrize(
        "uname,expected",
        [
            ("Linux x86_64", "linux-x86_64"),
            ("Linux aarch64", "linux-arm64"),
            ("Darwin arm64", "macos-arm64"),
            ("Darwin x86_64", "macos-x86_64"),
        ],
    )
    def test_mapping(self, uname, expected):
        assert provision.platform_cache_key(uname) == expected


# ---------------------------------------------------------------------------
# bootstrap_command
# ---------------------------------------------------------------------------


class TestBootstrapCommand:
    def test_shape(self, tmp_path, monkeypatch):
        monkeypatch.setattr(provision, "_executor_state_dir", lambda: tmp_path)
        command = provision.bootstrap_command(
            provision.parse_ssh_target("alice@gpu-01:2222")
        )
        # 交互式（无 BatchMode）、幂等装钥、公钥经 stdin 重定向
        assert "BatchMode" not in command
        assert "alice@gpu-01" in command
        assert "-p 2222" in command
        assert "authorized_keys" in command
        assert "grep -qxF" in command  # 幂等守卫（重复引导不重复 append）
        assert "id_ed25519.pub" in command
        assert "<" in command


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


class TestProbe:
    def test_success_bin_ok(self, monkeypatch):
        _patch_exec(
            monkeypatch,
            [
                _FakeProcess(
                    stdout=b"Linux x86_64\n/home/alice\n/bin/bash\n__BIN_OK__\n"
                )
            ],
        )
        target = provision.parse_ssh_target("alice@gpu-01")
        result = asyncio.run(provision.probe(target))
        assert result.uname == "Linux x86_64"
        assert result.remote_bin_ok is True
        assert result.platform_key == "linux-x86_64"
        assert result.home == "/home/alice"
        assert result.shell == "bash"

    def test_success_bin_missing(self, monkeypatch):
        _patch_exec(
            monkeypatch,
            [_FakeProcess(stdout=b"Linux x86_64\n/root\n/bin/zsh\n__BIN_MISSING__\n")],
        )
        result = asyncio.run(provision.probe(provision.parse_ssh_target("gpu-01")))
        assert result.remote_bin_ok is False
        assert result.home == "/root"
        assert result.shell == "zsh"

    def test_minimal_output_tolerated(self, monkeypatch):
        """老形状（无 home/shell 行）解析不炸——字段回落空串。"""
        _patch_exec(
            monkeypatch,
            [_FakeProcess(stdout=b"Linux x86_64\n__BIN_OK__\n")],
        )
        result = asyncio.run(provision.probe(provision.parse_ssh_target("gpu-01")))
        assert result.uname == "Linux x86_64"
        assert result.home == "" and result.shell == ""

    def test_auth_failure(self, monkeypatch):
        _patch_exec(
            monkeypatch,
            [
                _FakeProcess(
                    stderr=b"alice@gpu-01: Permission denied (publickey,password).",
                    code=255,
                )
            ],
        )
        with pytest.raises(provision.ProvisionError) as excinfo:
            asyncio.run(provision.probe(provision.parse_ssh_target("alice@gpu-01")))
        assert excinfo.value.step == "auth"

    def test_connect_failure(self, monkeypatch):
        _patch_exec(
            monkeypatch,
            [
                _FakeProcess(
                    stderr=b"ssh: connect to host gpu-01 port 22: Connection refused",
                    code=255,
                )
            ],
        )
        with pytest.raises(provision.ProvisionError) as excinfo:
            asyncio.run(provision.probe(provision.parse_ssh_target("gpu-01")))
        assert excinfo.value.step == "connect"


# ---------------------------------------------------------------------------
# ensure_remote_binary
# ---------------------------------------------------------------------------


class TestEnsureRemoteBinary:
    def test_skip_when_remote_ok(self, monkeypatch):
        calls = _patch_exec(monkeypatch, [])
        probe_result = provision.ProbeResult(uname="Linux x86_64", remote_bin_ok=True)
        asyncio.run(
            provision.ensure_remote_binary(
                provision.parse_ssh_target("gpu-01"), probe_result
            )
        )
        assert calls == []

    def test_missing_local_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(provision, "_executor_state_dir", lambda: tmp_path)
        probe_result = provision.ProbeResult(uname="Linux x86_64", remote_bin_ok=False)
        with pytest.raises(provision.ProvisionError) as excinfo:
            asyncio.run(
                provision.ensure_remote_binary(
                    provision.parse_ssh_target("gpu-01"), probe_result
                )
            )
        assert excinfo.value.step == "binary"
        assert "linux-x86_64" in str(excinfo.value)

    def test_upload_flow(self, tmp_path, monkeypatch):
        # 本地缓存就位
        cache = tmp_path / "bin" / "linux-x86_64"
        cache.mkdir(parents=True)
        binary = cache / "nova-executor"
        binary.write_bytes(b"\x7fELF fake")
        binary.chmod(0o755)
        monkeypatch.setattr(provision, "_executor_state_dir", lambda: tmp_path)

        calls = _patch_exec(
            monkeypatch,
            [_FakeProcess(), _FakeProcess(), _FakeProcess()],  # mkdir / scp / chmod+mv
        )
        progress = []
        probe_result = provision.ProbeResult(uname="Linux x86_64", remote_bin_ok=False)
        asyncio.run(
            provision.ensure_remote_binary(
                provision.parse_ssh_target("alice@gpu-01"),
                probe_result,
                on_progress=progress.append,
            )
        )
        assert len(calls) == 3
        assert calls[0][0] == "ssh" and "mkdir -p" in calls[0][-1]
        assert calls[1][0] == "scp"
        assert str(binary) in calls[1]
        assert (
            "alice@gpu-01:~/.nova/agent/executor/bin/.nova-executor.upload" in calls[1]
        )
        assert calls[2][0] == "ssh" and "mv" in calls[2][-1]
        assert progress and "linux-x86_64" in progress[0]

    def test_upload_failure(self, tmp_path, monkeypatch):
        cache = tmp_path / "bin" / "linux-x86_64"
        cache.mkdir(parents=True)
        binary = cache / "nova-executor"
        binary.write_bytes(b"x")
        binary.chmod(0o755)
        monkeypatch.setattr(provision, "_executor_state_dir", lambda: tmp_path)
        _patch_exec(
            monkeypatch,
            [_FakeProcess(), _FakeProcess(stderr=b"scp: write failed", code=1)],
        )
        probe_result = provision.ProbeResult(uname="Linux x86_64", remote_bin_ok=False)
        with pytest.raises(provision.ProvisionError) as excinfo:
            asyncio.run(
                provision.ensure_remote_binary(
                    provision.parse_ssh_target("gpu-01"), probe_result
                )
            )
        assert excinfo.value.step == "binary"


# ---------------------------------------------------------------------------
# spawn_remote
# ---------------------------------------------------------------------------


class TestSpawnRemote:
    def test_success(self, monkeypatch):
        listen = b'{"msg":"listening","addr":"ws://127.0.0.1:23456"}\n'
        calls = _patch_exec(monkeypatch, [_FakeProcess(stream_lines=[listen])])
        target = provision.parse_ssh_target("alice@gpu-01")
        probe_result = provision.ProbeResult(
            uname="Linux x86_64",
            remote_bin_ok=True,
            home="/home/alice",
            shell="bash",
        )
        handle = asyncio.run(provision.spawn_remote(target, probe_result))
        assert handle.url.startswith("ws://127.0.0.1:")
        assert len(handle.token) == 32
        assert handle.alive()
        # 探测信息随句柄传播（/executor 定远程 cwd 与 shell 用）
        assert handle.platform == "linux-x86_64"
        assert handle.default_cwd == "/home/alice"
        assert handle.remote_shell == "bash"
        args = calls[0]
        # 单 ssh 进程：-tt 远程 PTY + -L 隧道 + exec 远程命令 + token 随行
        joined = " ".join(args)
        assert "-tt" in args and "-L" in args and "127.0.0.1:" in joined
        remote_cmd = args[-1]
        assert remote_cmd.startswith(
            "TERM=dumb exec ~/.nova/agent/executor/bin/nova-executor"
        )
        assert f"--auth-token {handle.token}" in remote_cmd
        assert "BatchMode=yes" in joined

    def test_all_attempts_fail(self, monkeypatch):
        # 每次尝试都是 EOF（远程立即退出）
        procs = [
            _FakeProcess(stderr=b"bind: Address already in use", code=1)
            for _ in range(provision._SPAWN_PORT_ATTEMPTS)
        ]
        _patch_exec(monkeypatch, procs)
        with pytest.raises(provision.ProvisionError) as excinfo:
            asyncio.run(provision.spawn_remote(provision.parse_ssh_target("gpu-01")))
        assert excinfo.value.step == "spawn"


# ---------------------------------------------------------------------------
# provision 编排
# ---------------------------------------------------------------------------


class TestProvisionFlow:
    @pytest.fixture(autouse=True)
    def _patch_key(self, monkeypatch, tmp_path):
        monkeypatch.setattr(provision, "ensure_managed_key", lambda: tmp_path / "k")

    def _handle(self, target):
        return provision.SshRemoteHandle(
            target=target,
            url="ws://127.0.0.1:31000",
            token="t" * 32,
            process=_FakeProcess(),
        )

    def test_happy_path_no_bootstrap(self, monkeypatch):
        steps = []

        async def _probe(target):
            return provision.ProbeResult(uname="Linux x86_64", remote_bin_ok=True)

        async def _spawn(target, _probe_result=None):
            return self._handle(target)

        monkeypatch.setattr(provision, "probe", _probe)
        monkeypatch.setattr(provision, "spawn_remote", _spawn)
        target = provision.parse_ssh_target("alice@gpu-01")
        handle = asyncio.run(provision.provision(target, on_progress=steps.append))
        assert handle.url == "ws://127.0.0.1:31000"
        assert steps[-1].startswith("就绪")

    def test_auth_bootstrap_success(self, monkeypatch):
        probes = []

        async def _probe(target):
            probes.append(1)
            if len(probes) == 1:
                raise provision.ProvisionError("auth", "Permission denied")
            return provision.ProbeResult(uname="Linux x86_64", remote_bin_ok=True)

        async def _spawn(target, _probe_result=None):
            return self._handle(target)

        bootstraps = []

        async def _bootstrap(command):
            bootstraps.append(command)
            return 0

        monkeypatch.setattr(provision, "probe", _probe)
        monkeypatch.setattr(provision, "spawn_remote", _spawn)
        target = provision.parse_ssh_target("alice@gpu-01")
        asyncio.run(provision.provision(target, bootstrap=_bootstrap))
        assert len(probes) == 2  # 引导后重探
        assert len(bootstraps) == 1
        assert "authorized_keys" in bootstraps[0]

    def test_auth_without_bootstrap_gives_guidance(self, monkeypatch):
        async def _probe(target):
            raise provision.ProvisionError("auth", "Permission denied")

        monkeypatch.setattr(provision, "probe", _probe)
        with pytest.raises(provision.ProvisionError) as excinfo:
            asyncio.run(provision.provision(provision.parse_ssh_target("alice@gpu-01")))
        assert excinfo.value.step == "auth"
        assert "ssh-copy-id" in str(excinfo.value)

    def test_auth_bootstrap_failed(self, monkeypatch):
        async def _probe(target):
            raise provision.ProvisionError("auth", "Permission denied")

        async def _bootstrap(_command):
            return 1

        monkeypatch.setattr(provision, "probe", _probe)
        with pytest.raises(provision.ProvisionError) as excinfo:
            asyncio.run(
                provision.provision(
                    provision.parse_ssh_target("alice@gpu-01"),
                    bootstrap=_bootstrap,
                )
            )
        assert "exit 1" in str(excinfo.value)

    def test_connect_failure_never_bootstraps(self, monkeypatch):
        async def _probe(target):
            raise provision.ProvisionError("connect", "Connection refused")

        called = []

        async def _bootstrap(command):
            called.append(command)
            return 0

        monkeypatch.setattr(provision, "probe", _probe)
        with pytest.raises(provision.ProvisionError) as excinfo:
            asyncio.run(
                provision.provision(
                    provision.parse_ssh_target("gpu-01"), bootstrap=_bootstrap
                )
            )
        assert excinfo.value.step == "connect"
        assert called == []  # 网络问题不引导密码
