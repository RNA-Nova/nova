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
