"""协议类型测试"""

import base64

from nova_executor.protocol import (
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
    data = params.model_dump(by_alias=True)
    assert data == {"clientName": "test"}


def test_initialize_response_with_environment_info():
    """initialize 响应捎带 environmentInfo（含新增三字段）的解析"""
    from nova_executor.protocol import InitializeResponse

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


def test_initialize_response_without_environment_info():
    """旧服务端缺省形态：无 environmentInfo 字段 → None（回退单次调用）"""
    from nova_executor.protocol import InitializeResponse

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
    from nova_executor.protocol import FsWalkParams, WalkOptions, WalkOutcome

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
    from nova_executor.protocol import EnvironmentConfigReadParams

    params = EnvironmentConfigReadParams(
        cwd="file:///repo", configPaths=[["sandbox"], ["network", "mode"]]
    )
    assert params.model_dump(by_alias=True) == {
        "cwd": "file:///repo",
        "configPaths": [["sandbox"], ["network", "mode"]],
    }


def test_environment_config_read_response():
    """environmentConfig/read 响应解析（层栈 + error 字段 + 可选字段缺省）"""
    from nova_executor.protocol import EnvironmentConfigReadResponse

    response = EnvironmentConfigReadResponse.model_validate(
        {
            "userHomeDir": "file:///home/u",
            "executorHomeDir": "file:///home/u/.nova/executor",
            "hostname": "devbox",
            "config": {
                "layers": [
                    {
                        "source": "user:/home/u/.nova/executor/config.toml",
                        "baseDir": "file:///home/u/.nova/executor",
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
    assert response.executor_home_dir == "file:///home/u/.nova/executor"
    assert response.hostname == "devbox"
    assert response.config.cloud_insertion_index == 2
    user, project = response.config.layers
    assert user.source == "user:/home/u/.nova/executor/config.toml"
    assert user.format == "toml"
    assert user.error is None
    assert project.format == "json"
    assert project.error is not None and "failed to parse" in project.error


def test_environment_config_read_response_without_optional_fields():
    """可选字段（userHomeDir/hostname/error）缺省也能反序列化"""
    from nova_executor.protocol import EnvironmentConfigReadResponse

    response = EnvironmentConfigReadResponse.model_validate(
        {
            "executorHomeDir": "file:///home/u/.nova/executor",
            "config": {"layers": [], "cloudInsertionIndex": 0},
        }
    )
    assert response.user_home_dir is None
    assert response.hostname is None
    assert response.config.layers == []
