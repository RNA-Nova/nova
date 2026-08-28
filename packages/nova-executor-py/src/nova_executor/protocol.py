"""nova-executor JSON-RPC 协议类型和常量"""

from __future__ import annotations

import base64
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_serializer, field_validator

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
ENVIRONMENT_CONFIG_READ = "environmentConfig/read"
FS_READ_FILE = "fs/readFile"
FS_OPEN = "fs/open"
FS_READ_BLOCK = "fs/readBlock"
FS_CLOSE = "fs/close"
FS_READ_STREAM = "fs/readStream"
FS_READ_STREAM_CHUNK = "fs/readStream/chunk"
FS_READ_STREAM_DONE = "fs/readStream/done"
FS_WRITE_STREAM = "fs/writeStream"
FS_WRITE_STREAM_CHUNK = "fs/writeStream/chunk"
FS_WRITE_STREAM_DONE = "fs/writeStream/done"
FS_WRITE_FILE = "fs/writeFile"
FS_CREATE_DIRECTORY = "fs/createDirectory"
FS_GET_METADATA = "fs/getMetadata"
FS_CANONICALIZE = "fs/canonicalize"
FS_READ_DIRECTORY = "fs/readDirectory"
FS_WALK = "fs/walk"
FS_REMOVE = "fs/remove"
FS_COPY = "fs/copy"

#: 流式写入单块解码后字节上限（对齐服务端 file_write.rs 的
#: MAX_WRITE_STREAM_CHUNK_BYTES，超限时服务端转入 Failed 并在 done 报错）
MAX_WRITE_STREAM_CHUNK_BYTES = 4 * 1024 * 1024


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
    #: 初始化捎带的执行端环境元数据（形状同 environment/info，省一次往返；
    #: 旧服务端无此字段 → None，客户端回退单次 environment/info 调用）
    environment_info: EnvironmentInfo | None = Field(
        default=None, alias="environmentInfo"
    )


class EnvironmentInfo(BaseModel):
    shell: ShellInfo
    cwd: str | None = None
    user_home_dir: str | None = Field(default=None, alias="userHomeDir")
    platform_os: str | None = Field(default=None, alias="platformOs")
    temporary_directories: list[str] | None = Field(
        default=None, alias="temporaryDirectories"
    )
    temp_dir: str | None = Field(default=None, alias="tempDir")
    capabilities: EnvironmentCapabilities | None = None


class ShellInfo(BaseModel):
    name: str
    path: str


class EnvironmentCapabilities(BaseModel):
    network_proxy_launch: bool = Field(default=False, alias="networkProxyLaunch")
    environment_config_read: bool = Field(default=False, alias="environmentConfigRead")


class EnvironmentStatus(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# environmentConfig/read（v1.4 起——executor 代读本机配置层栈，不合并不裁决）
# ---------------------------------------------------------------------------


class EnvironmentConfigReadParams(BaseModel):
    """代读请求：cwd 定位 project 层，config_paths 为键路径选择器

    （每条路径至少一个键段，如 [["sandbox"], ["network", "mode"]]；
    至少一条路径，否则服务端 invalid_params——不允许整文档读取）。
    """

    cwd: str
    config_paths: list[list[str]] = Field(..., alias="configPaths")


class EnvironmentConfigLayer(BaseModel):
    """一个已选的 executor 本机配置层"""

    #: 层来源标记（不透明诊断串，形如 user:<绝对路径> / project:<绝对路径>）
    source: str
    #: 解释层内相对路径的基准目录（user 层 = executor home；project 层 = <cwd>/.nova）
    base_dir: str = Field(..., alias="baseDir")
    #: 层内容格式："toml"（user 层）| "json"（project 层）
    format: str
    #: 投影后的层内容原文（空层为其空文档形态：TOML "" / JSON "{}"）
    content: str
    #: 层读取/解析错误（文件缺失不算错误；解析失败时 content 为空、错误回本字段）
    error: str | None = None


class EnvironmentConfigLayerStack(BaseModel):
    """有序配置层栈（从低到高优先级：user 层在前、project 层在后，两层恒在）"""

    layers: list[EnvironmentConfigLayer]
    #: 预留对位字段（云托管层插入位）；nova 无云配置层，恒等于 len(layers)
    cloud_insertion_index: int = Field(..., alias="cloudInsertionIndex")


class EnvironmentConfigReadResponse(BaseModel):
    """executor 本机配置层栈与环境信息"""

    #: executor 用户家目录（客户端展开 ~ 的目标）
    user_home_dir: str | None = Field(default=None, alias="userHomeDir")
    #: executor 家目录（~/.nova/executor，或 NOVA_EXECUTOR_HOME 覆盖）
    executor_home_dir: str = Field(..., alias="executorHomeDir")
    #: executor 主机名（诊断用途）
    hostname: str | None = None
    config: EnvironmentConfigLayerStack


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

    @field_serializer("chunk")
    def serialize_chunk(self, v: bytes) -> str:
        """线上为 base64 字符串（Rust ByteChunk 透明序列化），否则 model_dump
        产出的 bytes 无法进 json.dumps"""
        return base64.b64encode(v).decode()


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
    #: 是否跟随符号链接；None = 不下发（服务端默认 true，保持旧行为）
    follow_symlinks: bool | None = Field(default=None, alias="followSymlinks")
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


# ---------------------------------------------------------------------------
# fs/writeStream（流式写入——readStream 的方向反转：客户端分片推，服务端顺序落盘）
# ---------------------------------------------------------------------------


class FsWriteStreamParams(BaseModel):
    """流式写入开句柄请求（打开即创建/截断，与 fs/writeFile 语义一致）"""

    handle_id: str = Field(..., alias="handleId")
    path: str
    sandbox: dict[str, Any] | None = None


class FsWriteStreamResponse(BaseModel):
    handle_id: str = Field(..., alias="handleId")


class FsWriteStreamChunkNotification(BaseModel):
    """流式写入数据块（客户端 → 服务端通知，无回执）"""

    handle_id: str = Field(..., alias="handleId")
    #: 从 0 开始的连续序号，服务端严格按序落盘（乱序即失败删半截文件）
    seq: int
    chunk: bytes
    #: 标记最后一个数据块；流仍须以 fs/writeStream/done 请求收尾确认
    eof: bool = False

    @field_validator("chunk", mode="before")
    @classmethod
    def decode_chunk(cls, v: Any) -> bytes:
        if isinstance(v, str):
            return base64.b64decode(v)
        return v

    @field_serializer("chunk")
    def serialize_chunk(self, v: bytes) -> str:
        """线上为 base64 字符串（Rust ByteChunk 透明序列化）"""
        return base64.b64encode(v).decode()


class FsWriteStreamDoneParams(BaseModel):
    handle_id: str = Field(..., alias="handleId")


class FsWriteStreamDoneResponse(BaseModel):
    handle_id: str = Field(..., alias="handleId")
    #: 实际落盘的总字节数
    total_bytes: int = Field(..., alias="totalBytes")


class FsWriteFileParams(BaseModel):
    path: str
    data_base64: str = Field(..., alias="dataBase64")
    #: 是否跟随符号链接；None = 不下发（服务端默认 true，保持旧行为）
    follow_symlinks: bool | None = Field(default=None, alias="followSymlinks")
    sandbox: dict[str, Any] | None = None


class FsCreateDirectoryParams(BaseModel):
    path: str
    recursive: bool | None = None
    #: 是否跟随符号链接；None = 不下发（服务端默认 true，保持旧行为）
    follow_symlinks: bool | None = Field(default=None, alias="followSymlinks")
    sandbox: dict[str, Any] | None = None


class FsGetMetadataParams(BaseModel):
    path: str
    #: 是否跟随符号链接；None = 不下发（服务端默认 true，保持旧行为）
    follow_symlinks: bool | None = Field(default=None, alias="followSymlinks")
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
    #: 是否跟随符号链接；None = 不下发（服务端默认 true，保持旧行为）
    follow_symlinks: bool | None = Field(default=None, alias="followSymlinks")
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
