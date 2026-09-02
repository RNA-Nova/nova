"""nova-executor JSON-RPC 协议类型和常量"""

from __future__ import annotations

import base64
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

# =============================================================================
# 方法常量
# =============================================================================

#: 客户端协议版本（与服务端 InitializeResponse.protocol_version 做 major 匹配）
PROTOCOL_VERSION = "1.0"

INITIALIZE = "initialize"
INITIALIZED = "initialized"
PROCESS_START = "process/start"
PROCESS_READ = "process/read"
PROCESS_WRITE = "process/write"
PROCESS_SIGNAL = "process/signal"
PROCESS_TERMINATE = "process/terminate"
PROCESS_OUTPUT = "process/output"
PROCESS_EXITED = "process/exited"
PROCESS_CLOSED = "process/closed"
ENVIRONMENT_INFO = "environment/info"
ENVIRONMENT_STATUS = "environment/status"
FS_READ_FILE = "fs/readFile"
FS_OPEN = "fs/open"
FS_READ_BLOCK = "fs/readBlock"
FS_CLOSE = "fs/close"
FS_READ_STREAM = "fs/readStream"
FS_READ_STREAM_CHUNK = "fs/readStream/chunk"
FS_READ_STREAM_DONE = "fs/readStream/done"
FS_WRITE_FILE = "fs/writeFile"
FS_CREATE_DIRECTORY = "fs/createDirectory"
FS_GET_METADATA = "fs/getMetadata"
FS_CANONICALIZE = "fs/canonicalize"
FS_READ_DIRECTORY = "fs/readDirectory"
FS_WALK = "fs/walk"
FS_REMOVE = "fs/remove"
FS_COPY = "fs/copy"


# =============================================================================
# 基础类型
# =============================================================================


class JsonRpcRequest(BaseModel):
    id: int | None = None
    method: str
    params: dict[str, Any] | None = None


class JsonRpcResponse(BaseModel):
    id: int | None = None
    result: Any | None = None
    error: JsonRpcError | None = None


class JsonRpcError(BaseModel):
    code: int
    message: str


class JsonRpcNotification(BaseModel):
    method: str
    params: dict[str, Any] | None = None


class ByteChunk(BaseModel):
    data: bytes

    @field_validator("data", mode="before")
    @classmethod
    def decode_base64(cls, v: Any) -> bytes:
        if isinstance(v, str):
            return base64.b64decode(v)
        return v

    def model_dump(self, **kwargs) -> dict[str, Any]:
        return {"data": base64.b64encode(self.data).decode()}


# =============================================================================
# 初始化
# =============================================================================


class InitializeParams(BaseModel):
    client_name: str = Field(..., alias="clientName")


class InitializeResponse(BaseModel):
    session_id: str = Field(..., alias="sessionId")
    protocol_version: str | None = Field(default=None, alias="protocolVersion")


class EnvironmentInfo(BaseModel):
    shell: ShellInfo
    cwd: str | None = None
    temporary_directories: list[str] | None = Field(
        default=None, alias="temporaryDirectories"
    )
    capabilities: EnvironmentCapabilities | None = None


class ShellInfo(BaseModel):
    name: str
    path: str


class EnvironmentCapabilities(BaseModel):
    network_proxy_launch: bool = Field(default=False, alias="networkProxyLaunch")
    environment_config_read: bool = Field(default=False, alias="environmentConfigRead")


class EnvironmentStatus(BaseModel):
    status: str


# =============================================================================
# 进程管理
# =============================================================================


class ProcessStartParams(BaseModel):
    process_id: str = Field(..., alias="processId")
    argv: list[str]
    cwd: str
    env: dict[str, str]
    tty: bool = False
    pipe_stdin: bool = Field(default=False, alias="pipeStdin")


class ProcessStartResponse(BaseModel):
    process_id: str = Field(..., alias="processId")
    sandbox_type: str | None = Field(default=None, alias="sandboxType")


class ProcessReadParams(BaseModel):
    process_id: str = Field(..., alias="processId")
    after_seq: int | None = Field(default=None, alias="afterSeq")
    max_bytes: int | None = Field(default=None, alias="maxBytes")
    wait_ms: int | None = Field(default=None, alias="waitMs")


class ProcessOutputChunk(BaseModel):
    seq: int
    stream: str
    chunk: bytes

    @field_validator("chunk", mode="before")
    @classmethod
    def decode_chunk(cls, v: Any) -> bytes:
        if isinstance(v, str):
            return base64.b64decode(v)
        return v


class ProcessReadResponse(BaseModel):
    chunks: list[ProcessOutputChunk]
    next_seq: int = Field(..., alias="nextSeq")
    exited: bool
    exit_code: int | None = Field(default=None, alias="exitCode")
    closed: bool
    failure: str | None = None
    sandbox_denied: bool = Field(default=False, alias="sandboxDenied")


class ProcessWriteParams(BaseModel):
    process_id: str = Field(..., alias="processId")
    chunk: bytes
    write_id: str = Field(..., alias="writeId")

    @field_validator("chunk", mode="before")
    @classmethod
    def decode_chunk(cls, v: Any) -> bytes:
        if isinstance(v, str):
            return base64.b64decode(v)
        return v


class ProcessWriteResponse(BaseModel):
    status: str


class ProcessSignalParams(BaseModel):
    process_id: str = Field(..., alias="processId")
    signal: str


class ProcessTerminateParams(BaseModel):
    process_id: str = Field(..., alias="processId")


class ProcessTerminateResponse(BaseModel):
    running: bool


class ProcessExitedNotification(BaseModel):
    process_id: str = Field(..., alias="processId")
    seq: int
    exit_code: int = Field(..., alias="exitCode")
    sandbox_denied: bool = Field(default=False, alias="sandboxDenied")


# =============================================================================
# 文件系统
# =============================================================================


class FsReadFileParams(BaseModel):
    path: str
    sandbox: dict[str, Any] | None = None


class FsReadFileResponse(BaseModel):
    data_base64: str = Field(..., alias="dataBase64")

    @property
    def data(self) -> bytes:
        return base64.b64decode(self.data_base64)


class FsOpenParams(BaseModel):
    handle_id: str = Field(..., alias="handleId")
    path: str
    sandbox: dict[str, Any] | None = None


class FsOpenResponse(BaseModel):
    handle_id: str = Field(..., alias="handleId")


class FsReadBlockParams(BaseModel):
    handle_id: str = Field(..., alias="handleId")
    offset: int
    len: int


class FsReadBlockResponse(BaseModel):
    chunk: bytes
    eof: bool

    @field_validator("chunk", mode="before")
    @classmethod
    def decode_chunk(cls, v: Any) -> bytes:
        if isinstance(v, str):
            return base64.b64decode(v)
        return v


class FsCloseParams(BaseModel):
    handle_id: str = Field(..., alias="handleId")


class FsReadStreamParams(BaseModel):
    handle_id: str = Field(..., alias="handleId")
    path: str
    offset: int = 0
    len: int | None = None
    block_size: int | None = Field(default=None, alias="blockSize")
    sandbox: dict[str, Any] | None = None


class FsReadStreamResponse(BaseModel):
    handle_id: str = Field(..., alias="handleId")
    total_size: int | None = Field(default=None, alias="totalSize")


class FsReadStreamChunkNotification(BaseModel):
    handle_id: str = Field(..., alias="handleId")
    seq: int
    chunk: bytes
    eof: bool

    @field_validator("chunk", mode="before")
    @classmethod
    def decode_chunk(cls, v: Any) -> bytes:
        if isinstance(v, str):
            return base64.b64decode(v)
        return v


class FsReadStreamDoneNotification(BaseModel):
    handle_id: str = Field(..., alias="handleId")
    total_bytes: int = Field(..., alias="totalBytes")
    error: str | None = None


class FsWriteFileParams(BaseModel):
    path: str
    data_base64: str = Field(..., alias="dataBase64")
    sandbox: dict[str, Any] | None = None


class FsCreateDirectoryParams(BaseModel):
    path: str
    recursive: bool | None = None
    sandbox: dict[str, Any] | None = None


class FsGetMetadataParams(BaseModel):
    path: str
    sandbox: dict[str, Any] | None = None


class FileMetadata(BaseModel):
    is_directory: bool = Field(..., alias="isDirectory")
    is_file: bool = Field(..., alias="isFile")
    is_symlink: bool = Field(..., alias="isSymlink")
    size: int
    created_at_ms: int = Field(..., alias="createdAtMs")
    modified_at_ms: int = Field(..., alias="modifiedAtMs")


class FsCanonicalizeParams(BaseModel):
    path: str
    sandbox: dict[str, Any] | None = None


class FsCanonicalizeResponse(BaseModel):
    path: str


class FsReadDirectoryParams(BaseModel):
    path: str
    sandbox: dict[str, Any] | None = None


class DirEntry(BaseModel):
    file_name: str = Field(..., alias="fileName")
    is_directory: bool = Field(..., alias="isDirectory")
    is_file: bool = Field(..., alias="isFile")


class FsReadDirectoryResponse(BaseModel):
    entries: list[DirEntry]


class FsRemoveParams(BaseModel):
    path: str
    recursive: bool | None = None
    force: bool | None = None
    sandbox: dict[str, Any] | None = None


class FsCopyParams(BaseModel):
    source_path: str = Field(..., alias="sourcePath")
    destination_path: str = Field(..., alias="destinationPath")
    recursive: bool
    sandbox: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# fs/walk（目录遍历——Rust 侧已实现，SDK 补齐）
# ---------------------------------------------------------------------------


class WalkOptions(BaseModel):
    """遍历界限（对齐 executor-file-system WalkOptions；缺省值不超服务端
    上限 depth=64/directories=10000/entries=50000，超限整请求被拒）。"""

    max_depth: int = Field(default=64, alias="maxDepth")
    max_directories: int = Field(default=10_000, alias="maxDirectories")
    max_entries: int = Field(default=50_000, alias="maxEntries")
    follow_directory_symlinks: bool = Field(
        default=False, alias="followDirectorySymlinks"
    )
    prune_hidden_directories: bool = Field(
        default=False, alias="pruneHiddenDirectories"
    )


class WalkEntry(BaseModel):
    path: str
    kind: str  # "file" | "directory"


class WalkError(BaseModel):
    path: str
    message: str


class WalkOutcome(BaseModel):
    entries: list[WalkEntry] = []
    errors: list[WalkError] = []
    truncated: bool = False


class FsWalkParams(BaseModel):
    path: str
    options: WalkOptions
    sandbox: dict[str, Any] | None = None
