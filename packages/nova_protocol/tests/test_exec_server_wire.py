"""exec-server 线上词汇测试（批次 A 随词汇迁入枢纽）"""

import base64

from nova_protocol import (
    FileMetadata,
    FsReadStreamParams,
    InitializeParams,
    ProcessOutputChunk,
    ProcessReadResponse,
    ProcessStartParams,
)


def test_initialize_params():
    """测试初始化参数序列化"""
    params = InitializeParams(clientName="test")
    # 全量 dump 含 resumeSessionId=None；线上握手以 exclude_none 省略（不开新会话语义）
    assert params.model_dump(by_alias=True) == {
        "clientName": "test",
        "resumeSessionId": None,
    }
    assert params.model_dump(by_alias=True, exclude_none=True) == {"clientName": "test"}


def test_initialize_params_with_resume_session_id():
    """resumeSessionId 显式携带（断线重连恢复会话 / 显式 resume 既有会话）"""
    params = InitializeParams(clientName="test", resumeSessionId="session-1")
    assert params.model_dump(by_alias=True, exclude_none=True) == {
        "clientName": "test",
        "resumeSessionId": "session-1",
    }


def test_initialize_response_with_environment_info():
    """initialize 响应捎带 environmentInfo（含新增三字段）的解析"""
    from nova_protocol import InitializeResponse

    response = InitializeResponse.model_validate(
        {
            "sessionId": "session-1",
            "protocolVersion": "1.0",
            "environmentInfo": {
                "shell": {"name": "zsh", "path": "/bin/zsh"},
                "cwd": "file:///Users/test",
                "userHomeDir": "file:///Users/test",
                "platformOs": "macos",
                "temporaryDirectories": ["file:///tmp"],
                "tempDir": "file:///tmp",
                "capabilities": {"readStream": True},
            },
        }
    )
    info = response.environment_info
    assert info is not None
    assert info.user_home_dir == "file:///Users/test"
    assert info.platform_os == "macos"
    assert info.temp_dir == "file:///tmp"
    assert info.temporary_directories == ["file:///tmp"]


def test_environment_capabilities_full_wire_shape():
    """能力位镜像完整性：9 位全量（对照 RS `EnvironmentCapabilities` 与
    `local()` 宣告值）——camelCase 别名逐一解析、缺省全 false、显式值
    roundtrip 不丢位（防漏位）。

    RS `local()` 语义（py 侧只测模型 roundtrip，不涉平台分支）：
    networkProxyLaunch/environmentConfigRead/sandboxedFileStreaming/
    fileWriteStreaming/httpHeaderEnvVars 恒 true；shellSnapshotV2 按
    cfg(unix)、windowsMxc 按 windows 可用性、两个 linux 位按 cfg(linux)
    如实 true/false。
    """
    from nova_protocol import EnvironmentCapabilities

    # 9 位全量显式 true：逐位钉住 camelCase 别名 → snake 属性映射
    wire_full = {
        "networkProxyLaunch": True,
        "environmentConfigRead": True,
        "sandboxedFileStreaming": True,
        "fileWriteStreaming": True,
        "httpHeaderEnvVars": True,
        "shellSnapshotV2": True,
        "windowsMxc": True,
        "linuxRootWritePreservesDevices": True,
        "linuxApprovedRootWritePreservesRestrictions": True,
    }
    caps = EnvironmentCapabilities.model_validate(wire_full)
    assert caps.network_proxy_launch is True
    assert caps.environment_config_read is True
    assert caps.sandboxed_file_streaming is True
    assert caps.file_write_streaming is True
    assert caps.http_header_env_vars is True
    assert caps.shell_snapshot_v2 is True
    assert caps.windows_mxc is True
    assert caps.linux_root_write_preserves_devices is True
    assert caps.linux_approved_root_write_preserves_restrictions is True

    # 显式值 roundtrip：dump 回 9 位全量线上形态，再解析不丢字段
    # （RS 两个 linux 位 false 时线上省略，serde default 等价于显式 false——
    # py 模型恒出全键，语义一致）
    wire = caps.model_dump(by_alias=True)
    assert wire == wire_full
    assert EnvironmentCapabilities.model_validate(wire) == caps

    # 缺省（旧服务端省略能力位）：9 位逐一回退 false（对位 serde default）
    defaults = EnvironmentCapabilities.model_validate({})
    assert defaults.network_proxy_launch is False
    assert defaults.environment_config_read is False
    assert defaults.sandboxed_file_streaming is False
    assert defaults.file_write_streaming is False
    assert defaults.http_header_env_vars is False
    assert defaults.shell_snapshot_v2 is False
    assert defaults.windows_mxc is False
    assert defaults.linux_root_write_preserves_devices is False
    assert defaults.linux_approved_root_write_preserves_restrictions is False
    assert defaults.model_dump(by_alias=True) == {
        alias: False for alias in wire_full
    }

    # v1.6 撤除的端点位不得再出现在线上形态
    assert "readStream" not in wire
    assert "writeStream" not in wire


def test_initialize_response_without_environment_info():
    """旧服务端缺省形态：无 environmentInfo 字段 → None（回退单次调用）"""
    from nova_protocol import InitializeResponse

    response = InitializeResponse.model_validate(
        {"sessionId": "session-1", "protocolVersion": "1.0"}
    )
    assert response.environment_info is None


def test_process_start_params():
    """测试进程启动参数序列化"""
    params = ProcessStartParams(
        processId="test",
        argv=["echo", "hello"],
        cwd="file:///tmp",
        env={},
        tty=False,
        pipeStdin=False,
    )
    data = params.model_dump(by_alias=True)
    assert data["processId"] == "test"
    assert data["argv"] == ["echo", "hello"]


def test_process_output_chunk_decode():
    """测试进程输出块 base64 解码"""
    chunk = ProcessOutputChunk(
        seq=1,
        stream="stdout",
        chunk=base64.b64encode(b"hello").decode(),
    )
    assert chunk.chunk == b"hello"


def test_fs_read_stream_params():
    """测试流式读取参数序列化"""
    params = FsReadStreamParams(
        handleId="test",
        path="file:///tmp/test.txt",
        blockSize=256 * 1024,
    )
    data = params.model_dump(by_alias=True)
    assert data["handleId"] == "test"
    assert data["blockSize"] == 256 * 1024


def test_file_metadata():
    """测试文件元数据反序列化"""
    data = {
        "isDirectory": False,
        "isFile": True,
        "isSymlink": False,
        "size": 1024,
        "createdAtMs": 1234567890,
        "modifiedAtMs": 1234567891,
    }
    meta = FileMetadata.model_validate(data)
    assert meta.is_file
    assert meta.size == 1024


def test_walk_params_and_outcome():
    """fs/walk 参数序列化与结果反序列化（camelCase 对齐 Rust 契约）。"""
    from nova_protocol import FsWalkParams, WalkOptions, WalkOutcome

    params = FsWalkParams(
        path="file:///home/user",
        options=WalkOptions(maxDepth=8, maxEntries=500, followDirectorySymlinks=True),
    )
    data = params.model_dump(by_alias=True)
    assert data["path"] == "file:///home/user"
    assert data["options"]["maxDepth"] == 8
    assert data["options"]["maxEntries"] == 500
    assert data["options"]["followDirectorySymlinks"] is True
    # 默认界限（不传时由 SDK 默认值兜底）
    default_options = WalkOptions().model_dump(by_alias=True)
    assert default_options["maxDepth"] == 64
    assert default_options["maxEntries"] == 50_000

    outcome = WalkOutcome.model_validate(
        {
            "entries": [
                {"path": "file:///a/b.py", "kind": "file"},
                {"path": "file:///a/c", "kind": "directory"},
            ],
            "errors": [{"path": "file:///a/deny", "message": "permission denied"}],
            "truncated": False,
        }
    )
    assert len(outcome.entries) == 2
    assert outcome.entries[0].kind == "file"
    assert outcome.entries[1].kind == "directory"
    assert outcome.errors[0].message == "permission denied"
    assert outcome.truncated is False


def test_environment_config_read_params():
    """environmentConfig/read 请求参数序列化（camelCase 对齐 Rust 契约）"""
    from nova_protocol import EnvironmentConfigReadParams

    params = EnvironmentConfigReadParams(
        cwd="file:///repo", configPaths=[["sandbox"], ["network", "mode"]]
    )
    assert params.model_dump(by_alias=True) == {
        "cwd": "file:///repo",
        "configPaths": [["sandbox"], ["network", "mode"]],
    }


def test_environment_config_read_response():
    """environmentConfig/read 响应解析（层栈 + error 字段 + 可选字段缺省）"""
    from nova_protocol import EnvironmentConfigReadResponse

    response = EnvironmentConfigReadResponse.model_validate(
        {
            "userHomeDir": "file:///home/u",
            "executorHomeDir": "file:///home/u/.nova/exec-server",
            "hostname": "devbox",
            "config": {
                "layers": [
                    {
                        "source": "user:/home/u/.nova/exec-server/config.toml",
                        "baseDir": "file:///home/u/.nova/exec-server",
                        "format": "toml",
                        "content": '[sandbox]\nlevel = "workspace-write"\n',
                    },
                    {
                        "source": "project:/repo/.nova/settings.json",
                        "baseDir": "file:///repo/.nova",
                        "format": "json",
                        "content": "",
                        "error": "failed to parse `/repo/.nova/settings.json`: ...",
                    },
                ],
                "cloudInsertionIndex": 2,
            },
        }
    )

    assert response.user_home_dir == "file:///home/u"
    assert response.executor_home_dir == "file:///home/u/.nova/exec-server"
    assert response.hostname == "devbox"
    assert response.config.cloud_insertion_index == 2
    user, project = response.config.layers
    assert user.source == "user:/home/u/.nova/exec-server/config.toml"
    assert user.format == "toml"
    assert user.error is None
    assert project.format == "json"
    assert project.error is not None and "failed to parse" in project.error


def test_environment_config_read_response_without_optional_fields():
    """可选字段（userHomeDir/hostname/error）缺省也能反序列化"""
    from nova_protocol import EnvironmentConfigReadResponse

    response = EnvironmentConfigReadResponse.model_validate(
        {
            "executorHomeDir": "file:///home/u/.nova/exec-server",
            "config": {"layers": [], "cloudInsertionIndex": 0},
        }
    )
    assert response.user_home_dir is None
    assert response.hostname is None
    assert response.config.layers == []


def test_environment_info_executor_metadata_fields():
    """EnvironmentInfo 新增 executorVersion/providerId/prependPathDirs 的线上
    roundtrip（对位上游 EnvironmentInfo additive 字段）"""
    from nova_protocol import EnvironmentInfo

    info = EnvironmentInfo.model_validate(
        {
            "shell": {"name": "zsh", "path": "/bin/zsh"},
            "executorVersion": "1.2.3-alpha.4",
            "providerId": "commit-abc:target-x86_64",
            "cwd": "file:///Users/test",
            "prependPathDirs": ["file:///C:/tools/bin", "file:///D:/tools/bin"],
        }
    )
    assert info.executor_version == "1.2.3-alpha.4"
    assert info.provider_id == "commit-abc:target-x86_64"
    assert info.prepend_path_dirs == ["file:///C:/tools/bin", "file:///D:/tools/bin"]
    wire = info.model_dump(by_alias=True)
    assert wire["executorVersion"] == "1.2.3-alpha.4"
    assert wire["providerId"] == "commit-abc:target-x86_64"
    assert wire["prependPathDirs"] == ["file:///C:/tools/bin", "file:///D:/tools/bin"]
    # roundtrip：线上形态再解析不丢字段
    assert EnvironmentInfo.model_validate(wire) == info


def test_environment_info_legacy_payload_defaults():
    """旧 executor 缺席三字段 → 默认值（executorVersion "0.0.0" 对位上游
    unknown_executor_version），反序列化不炸"""
    from nova_protocol import EnvironmentInfo

    info = EnvironmentInfo.model_validate(
        {"shell": {"name": "zsh", "path": "/bin/zsh"}}
    )
    assert info.executor_version == "0.0.0"
    assert info.provider_id is None
    assert info.prepend_path_dirs == []


def test_process_start_params_metadata_roundtrip():
    """ExecParams.metadata{threadId?,toolCallId?}（工具归因，非授权）roundtrip；
    旧客户端省略 → None"""
    from nova_protocol import ExecMetadata

    params = ProcessStartParams(
        processId="p1",
        argv=["echo", "hi"],
        cwd="file:///tmp",
        env={},
        metadata=ExecMetadata(
            threadId="018f3d2a-7c4e-7b2a-9d1e-1234567890ab", toolCallId="call-1"
        ),
    )
    wire = params.model_dump(by_alias=True, exclude_none=True)
    assert wire["metadata"] == {
        "threadId": "018f3d2a-7c4e-7b2a-9d1e-1234567890ab",
        "toolCallId": "call-1",
    }
    again = ProcessStartParams.model_validate(wire)
    assert again.metadata is not None
    assert again.metadata.thread_id == "018f3d2a-7c4e-7b2a-9d1e-1234567890ab"
    assert again.metadata.tool_call_id == "call-1"

    legacy = ProcessStartParams.model_validate(
        {"processId": "p1", "argv": ["echo"], "cwd": "file:///tmp", "env": {}}
    )
    assert legacy.metadata is None
    # 部分字段缺席（只有 toolCallId）也能解析
    partial = ExecMetadata.model_validate({"toolCallId": "call-2"})
    assert partial.thread_id is None


def test_managed_network_sandbox_context_unix_socket_fields():
    """ManagedNetworkSandboxContext 新增 allowUnixSockets /
    dangerouslyAllowAllUnixSockets roundtrip；旧数据缺席=默认"""
    from nova_protocol import ManagedNetworkSandboxContext

    ctx = ManagedNetworkSandboxContext.model_validate(
        {
            "loopbackPorts": [19001],
            "allowLocalBinding": True,
            "allowUnixSockets": ["/tmp/allowed.sock"],
            "dangerouslyAllowAllUnixSockets": True,
        }
    )
    assert ctx.allow_unix_sockets == ["/tmp/allowed.sock"]
    assert ctx.dangerously_allow_all_unix_sockets is True
    wire = ctx.model_dump(by_alias=True)
    assert wire["allowUnixSockets"] == ["/tmp/allowed.sock"]
    assert wire["dangerouslyAllowAllUnixSockets"] is True
    assert ManagedNetworkSandboxContext.model_validate(wire) == ctx

    legacy = ManagedNetworkSandboxContext.model_validate({"loopbackPorts": [19001]})
    assert legacy.allow_unix_sockets == []
    assert legacy.dangerously_allow_all_unix_sockets is False


def test_fs_open_params_mode_defaults_read():
    """fs/open 新增 mode（read|replace，缺省 read——旧调用方保持只读打开）"""
    from nova_protocol import FsOpenMode, FsOpenParams

    legacy = FsOpenParams.model_validate({"handleId": "h1", "path": "file:///tmp/a"})
    assert legacy.mode is FsOpenMode.READ
    assert legacy.model_dump(by_alias=True)["mode"] == "read"

    replace = FsOpenParams(handleId="h2", path="file:///tmp/b", mode=FsOpenMode.REPLACE)
    wire = replace.model_dump(by_alias=True)
    assert wire["mode"] == "replace"
    assert FsOpenParams.model_validate(wire).mode is FsOpenMode.REPLACE


def test_windows_sandbox_level_mxc_roundtrip():
    """windowsSandboxLevel 枚举新增 mxc 值（独立沙箱实现而非 RestrictedToken 档位）"""
    from nova_protocol import FileSystemSandboxContext, WindowsSandboxLevel

    ctx = FileSystemSandboxContext(windowsSandboxLevel=WindowsSandboxLevel.MXC)
    wire = ctx.model_dump(by_alias=True)
    assert wire["windowsSandboxLevel"] == "mxc"
    assert (
        FileSystemSandboxContext.model_validate(wire).windows_sandbox_level
        is WindowsSandboxLevel.MXC
    )


def test_windows_sandbox_private_desktop_removed_from_wire():
    """windowsSandboxPrivateDesktop 已从线上撤除（对位上游删除）：dump 不再出现；
    旧数据残留该字段时解析忽略不炸"""
    from nova_protocol import FileSystemSandboxContext

    wire = FileSystemSandboxContext().model_dump(by_alias=True)
    assert "windowsSandboxPrivateDesktop" not in wire
    legacy = FileSystemSandboxContext.model_validate(
        {"windowsSandboxPrivateDesktop": True, "windowsSandboxLevel": "elevated"}
    )
    assert legacy.windows_sandbox_level.value == "elevated"


def test_permission_profile_wire_shape_matches_codex_golden():
    """permissions 内层命名金标（对位 codex exec-server-protocol tests 的线上字面量）：

    历史 bug：py 曾把内层键配成 camelCase alias（fileSystem/globScanMaxDepth/
    missingPathBehavior），而 codex/RS 线上是 snake_case——py 发的带沙箱请求
    在 RS 端反序列化必败。本用例把 codex 自家金标字面量钉为唯一形状。
    """
    from nova_protocol import (
        ExecFileSystemPath,
        ExecFileSystemSandboxEntry,
        ExecManagedFileSystemPermissions,
        ExecPermissionProfile,
        FileSystemAccessMode,
        NetworkSandboxPolicy,
    )

    profile = ExecPermissionProfile(
        type="managed",
        file_system=ExecManagedFileSystemPermissions(
            type="restricted",
            entries=[
                ExecFileSystemSandboxEntry(
                    path=ExecFileSystemPath(type="path", path="file:///C:/a%20b"),
                    access=FileSystemAccessMode.READ,
                ),
                ExecFileSystemSandboxEntry(
                    path=ExecFileSystemPath(type="path", path="file://host/s/a%20b"),
                    access=FileSystemAccessMode.READ,
                ),
            ],
        ),
        network=NetworkSandboxPolicy.RESTRICTED,
    )

    # 与 codex exec-server-protocol tests 的线上字面量逐字符一致
    # （exclude_none 对齐 codex serde skip_serializing_if 的紧凑形态；
    #  py 出货路径带 null 亦可——RS serde 对 Option 字段 null→None 容忍）
    assert profile.model_dump(by_alias=True, exclude_none=True) == {
        "type": "managed",
        "file_system": {
            "type": "restricted",
            "entries": [
                {"path": {"type": "path", "path": "file:///C:/a%20b"}, "access": "read"},
                {"path": {"type": "path", "path": "file://host/s/a%20b"}, "access": "read"},
            ],
        },
        "network": "restricted",
    }
    # 同一字面量 roundtrip 不丢字段
    assert ExecPermissionProfile.model_validate(
        profile.model_dump(by_alias=True, exclude_none=True)
    ) == profile
