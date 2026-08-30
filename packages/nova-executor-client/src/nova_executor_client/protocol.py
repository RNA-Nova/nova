"""nova-executor JSON-RPC 协议类型和常量"""

from __future__ import annotations

import base64
from enum import Enum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)

# =============================================================================
# 方法常量
# =============================================================================

#: 客户端协议版本（与服务端 InitializeResponse.protocol_version 做 major 匹配；
#: 跟随服务端 crates/executor-protocol/src/lib.rs::PROTOCOL_VERSION）
PROTOCOL_VERSION = "1.4"

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
NETWORK_POLICY_REQUEST = "network/policyRequest"
NETWORK_POLICY_DECISION = "network/policyDecision"
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
    model_config = ConfigDict(populate_by_name=True)
    id: int | None = None
    method: str
    params: dict[str, Any] | None = None


class JsonRpcResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: int | None = None
    result: Any | None = None
    error: JsonRpcError | None = None


class JsonRpcError(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    code: int
    message: str


class JsonRpcNotification(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    method: str
    params: dict[str, Any] | None = None


class ByteChunk(BaseModel):
    """透明字节块（对位 Rust `#[serde(transparent)] ByteChunk`）——线上即裸 base64 字符串"""

    model_config = ConfigDict(populate_by_name=True)
    data: bytes

    @model_validator(mode="before")
    @classmethod
    def _unwrap_transparent(cls, v: Any) -> Any:
        """透明形态出入：线上裸 base64 字符串先解包为 {"data": ...} 再进字段校验"""
        if isinstance(v, str):
            return {"data": v}
        return v

    @field_validator("data", mode="before")
    @classmethod
    def decode_base64(cls, v: Any) -> bytes:
        if isinstance(v, str):
            return base64.b64decode(v)
        return v

    @model_serializer
    def _to_base64(self) -> str:
        """序列化为裸 base64 字符串（透明形态），非 {"data": ...} 包装对象"""
        return base64.b64encode(self.data).decode()


# =============================================================================
# 初始化
# =============================================================================


class InitializeParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    client_name: str = Field(..., alias="clientName")
    #: 恢复既有会话（进程/文件句柄随会话存活）；None = 不下发，开新会话
    resume_session_id: str | None = Field(default=None, alias="resumeSessionId")


class InitializeResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    session_id: str = Field(..., alias="sessionId")
    protocol_version: str | None = Field(default=None, alias="protocolVersion")
    #: 初始化捎带的执行端环境元数据（形状同 environment/info，省一次往返；
    #: 旧服务端无此字段 → None，客户端回退单次 environment/info 调用）
    environment_info: EnvironmentInfo | None = Field(
        default=None, alias="environmentInfo"
    )


class EnvironmentInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
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
    model_config = ConfigDict(populate_by_name=True)
    name: str
    path: str


class EnvironmentCapabilities(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    network_proxy_launch: bool = Field(default=False, alias="networkProxyLaunch")
    environment_config_read: bool = Field(default=False, alias="environmentConfigRead")


class EnvironmentStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    status: str


# ---------------------------------------------------------------------------
# environmentConfig/read（v1.4 起——executor 代读本机配置层栈，不合并不裁决）
# ---------------------------------------------------------------------------


class EnvironmentConfigReadParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    """代读请求：cwd 定位 project 层，config_paths 为键路径选择器

    （每条路径至少一个键段，如 [["sandbox"], ["network", "mode"]]；
    至少一条路径，否则服务端 invalid_params——不允许整文档读取）。
    """

    cwd: str
    config_paths: list[list[str]] = Field(..., alias="configPaths")


class EnvironmentConfigLayer(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
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
    model_config = ConfigDict(populate_by_name=True)
    """有序配置层栈（从低到高优先级：user 层在前、project 层在后，两层恒在）"""

    layers: list[EnvironmentConfigLayer]
    #: 预留对位字段（云托管层插入位）；nova 无云配置层，恒等于 len(layers)
    cloud_insertion_index: int = Field(..., alias="cloudInsertionIndex")


class EnvironmentConfigReadResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
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
    model_config = ConfigDict(populate_by_name=True)
    process_id: str = Field(..., alias="processId")
    argv: list[str]
    cwd: str
    env: dict[str, str]
    tty: bool = False
    pipe_stdin: bool = Field(default=False, alias="pipeStdin")
    #: arg0 覆盖（真实 argv[0] 与可执行路径分离）
    arg0: str | None = Field(default=None, alias="arg0")
    #: 子进程环境策略（wire：ExecEnvPolicy）
    env_policy: dict[str, Any] | None = Field(default=None, alias="envPolicy")
    #: shell 快照请求（wire：ShellSnapshotRequest {scopeId, shell}）
    shell_snapshot: dict[str, Any] | None = Field(default=None, alias="shellSnapshot")
    #: 文件系统沙箱上下文（wire：FileSystemSandboxContext，编译后权限档形态）
    sandbox: dict[str, Any] | None = None
    #: 托管网络强制（fail-closed：无 managedNetwork 细节时拒绝放行）
    enforce_managed_network: bool = Field(default=False, alias="enforceManagedNetwork")
    #: 托管网络细节（loopback 代理端口 + 本地绑定许可）
    managed_network: dict[str, Any] | None = Field(default=None, alias="managedNetwork")
    #: executor 本地代理启动配置（透传 dict，wire：RemoteNetworkProxyLaunchConfig）
    network_proxy: dict[str, Any] | None = Field(default=None, alias="networkProxy")


class ProcessStartResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    process_id: str = Field(..., alias="processId")
    sandbox_type: str | None = Field(default=None, alias="sandboxType")


class ProcessReadParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    process_id: str = Field(..., alias="processId")
    after_seq: int | None = Field(default=None, alias="afterSeq")
    max_bytes: int | None = Field(default=None, alias="maxBytes")
    wait_ms: int | None = Field(default=None, alias="waitMs")


class ProcessOutputChunk(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
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
    model_config = ConfigDict(populate_by_name=True)
    chunks: list[ProcessOutputChunk]
    next_seq: int = Field(..., alias="nextSeq")
    exited: bool
    exit_code: int | None = Field(default=None, alias="exitCode")
    closed: bool
    failure: str | None = None
    sandbox_denied: bool = Field(default=False, alias="sandboxDenied")


class ProcessWriteParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
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
    model_config = ConfigDict(populate_by_name=True)
    status: str


class ProcessSignalParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    process_id: str = Field(..., alias="processId")
    signal: str


class ProcessTerminateParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    process_id: str = Field(..., alias="processId")


class ProcessTerminateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    running: bool


class ProcessExitedNotification(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    process_id: str = Field(..., alias="processId")
    seq: int
    exit_code: int = Field(..., alias="exitCode")
    sandbox_denied: bool = Field(default=False, alias="sandboxDenied")


# =============================================================================
# 文件系统
# =============================================================================


class FsReadFileParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    path: str
    #: 是否跟随符号链接；None = 不下发（服务端默认 true，保持旧行为）
    follow_symlinks: bool | None = Field(default=None, alias="followSymlinks")
    sandbox: dict[str, Any] | None = None


class FsReadFileResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    data_base64: str = Field(..., alias="dataBase64")

    @property
    def data(self) -> bytes:
        return base64.b64decode(self.data_base64)


class FsOpenParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")
    path: str
    sandbox: dict[str, Any] | None = None


class FsOpenResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")


class FsReadBlockParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")
    offset: int
    len: int


class FsReadBlockResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    chunk: bytes
    eof: bool

    @field_validator("chunk", mode="before")
    @classmethod
    def decode_chunk(cls, v: Any) -> bytes:
        if isinstance(v, str):
            return base64.b64decode(v)
        return v


class FsCloseParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")


class FsReadStreamParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")
    path: str
    offset: int = 0
    len: int | None = None
    block_size: int | None = Field(default=None, alias="blockSize")
    sandbox: dict[str, Any] | None = None


class FsReadStreamResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")
    total_size: int | None = Field(default=None, alias="totalSize")


class FsReadStreamChunkNotification(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
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
    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")
    total_bytes: int = Field(..., alias="totalBytes")
    error: str | None = None


# ---------------------------------------------------------------------------
# fs/writeStream（流式写入——readStream 的方向反转：客户端分片推，服务端顺序落盘）
# ---------------------------------------------------------------------------


class FsWriteStreamParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    """流式写入开句柄请求（打开即创建/截断，与 fs/writeFile 语义一致）"""

    handle_id: str = Field(..., alias="handleId")
    path: str
    sandbox: dict[str, Any] | None = None


class FsWriteStreamResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")


class FsWriteStreamChunkNotification(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
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
    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")


class FsWriteStreamDoneResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")
    #: 实际落盘的总字节数
    total_bytes: int = Field(..., alias="totalBytes")


class FsWriteFileParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    path: str
    data_base64: str = Field(..., alias="dataBase64")
    #: 是否跟随符号链接；None = 不下发（服务端默认 true，保持旧行为）
    follow_symlinks: bool | None = Field(default=None, alias="followSymlinks")
    sandbox: dict[str, Any] | None = None


class FsCreateDirectoryParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    path: str
    recursive: bool | None = None
    #: 是否跟随符号链接；None = 不下发（服务端默认 true，保持旧行为）
    follow_symlinks: bool | None = Field(default=None, alias="followSymlinks")
    sandbox: dict[str, Any] | None = None


class FsGetMetadataParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    path: str
    #: 是否跟随符号链接；None = 不下发（服务端默认 true，保持旧行为）
    follow_symlinks: bool | None = Field(default=None, alias="followSymlinks")
    sandbox: dict[str, Any] | None = None


class FileMetadata(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    is_directory: bool = Field(..., alias="isDirectory")
    is_file: bool = Field(..., alias="isFile")
    is_symlink: bool = Field(..., alias="isSymlink")
    size: int
    created_at_ms: int = Field(..., alias="createdAtMs")
    modified_at_ms: int = Field(..., alias="modifiedAtMs")


class FsCanonicalizeParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    path: str
    sandbox: dict[str, Any] | None = None


class FsCanonicalizeResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    path: str


class FsReadDirectoryParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    path: str
    sandbox: dict[str, Any] | None = None


class DirEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    file_name: str = Field(..., alias="fileName")
    is_directory: bool = Field(..., alias="isDirectory")
    is_file: bool = Field(..., alias="isFile")


class FsReadDirectoryResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    entries: list[DirEntry]


class FsRemoveParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    path: str
    recursive: bool | None = None
    force: bool | None = None
    #: 是否跟随符号链接；None = 不下发（服务端默认 true，保持旧行为）
    follow_symlinks: bool | None = Field(default=None, alias="followSymlinks")
    sandbox: dict[str, Any] | None = None


class FsCopyParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    source_path: str = Field(..., alias="sourcePath")
    destination_path: str = Field(..., alias="destinationPath")
    recursive: bool
    sandbox: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# fs/walk（目录遍历——Rust 侧已实现，SDK 补齐）
# ---------------------------------------------------------------------------


class WalkOptions(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
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
    model_config = ConfigDict(populate_by_name=True)
    path: str
    kind: str  # "file" | "directory"


class WalkError(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    path: str
    message: str


class WalkOutcome(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    entries: list[WalkEntry] = []
    errors: list[WalkError] = []
    truncated: bool = False


class FsWalkParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    path: str
    options: WalkOptions
    sandbox: dict[str, Any] | None = None


# =============================================================================
# http/request（executor 代发 HTTP）
# =============================================================================

HTTP_REQUEST = "http/request"
HTTP_REQUEST_BODY_DELTA = "http/request/bodyDelta"


class HttpRedirectPolicy(str, Enum):
    FOLLOW = "follow"
    STOP = "stop"


class HttpHeader(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    value: str


class HttpRequestParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    method: str
    url: str
    headers: list[HttpHeader] = Field(default_factory=list)
    body: ByteChunk | None = Field(default=None, alias="bodyBase64")
    timeout_ms: int | None = Field(default=None, alias="timeoutMs")
    redirect_policy: HttpRedirectPolicy = Field(
        default=HttpRedirectPolicy.FOLLOW, alias="redirectPolicy"
    )
    request_id: str = Field(..., alias="requestId")
    stream_response: bool = Field(default=False, alias="streamResponse")


class HttpRequestResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    status: int
    headers: list[HttpHeader] = Field(default_factory=list)
    body: ByteChunk = Field(alias="bodyBase64")


class HttpRequestBodyDeltaNotification(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    request_id: str = Field(..., alias="requestId")
    seq: int
    delta: ByteChunk = Field(alias="deltaBase64")
    done: bool = False
    error: str | None = None


# =============================================================================
# 沙箱上下文（process/start 的 sandbox 参数；wire 形态对齐
# executor-file-system 的 FileSystemSandboxContext / ExecPermissionProfile）
# =============================================================================


class NetworkSandboxPolicy(str, Enum):
    RESTRICTED = "restricted"
    ENABLED = "enabled"


class WindowsSandboxLevel(str, Enum):
    DISABLED = "disabled"
    RESTRICTED_TOKEN = "restricted-token"
    ELEVATED = "elevated"


class FileSystemAccessMode(str, Enum):
    READ = "read"
    WRITE = "write"
    DENY = "deny"


class WindowsSandboxProxySettingsMode(str, Enum):
    RECONCILE = "reconcile"
    PRESERVE = "preserve"


class ExecFileSystemPath(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    """沙箱条目路径（wire：internally-tagged "type"）"""

    type: Literal["path", "glob_pattern", "special"]
    path: str | None = None
    pattern: str | None = None
    special: str | None = None


class ExecFileSystemSandboxEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    path: ExecFileSystemPath
    access: FileSystemAccessMode
    missing_path_behavior: str | None = Field(default=None, alias="missingPathBehavior")


class ExecManagedFileSystemPermissions(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    """wire：internally-tagged "type"（restricted/unrestricted）"""

    type: Literal["restricted", "unrestricted"] = "restricted"
    entries: list[ExecFileSystemSandboxEntry] = Field(default_factory=list)
    glob_scan_max_depth: int | None = Field(default=None, alias="globScanMaxDepth")


class ExecPermissionProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    """wire：internally-tagged "type"（managed/disabled/external）"""

    type: Literal["managed", "disabled", "external"] = "managed"
    file_system: ExecManagedFileSystemPermissions = Field(
        default_factory=ExecManagedFileSystemPermissions,
        alias="fileSystem",
    )
    network: NetworkSandboxPolicy = NetworkSandboxPolicy.RESTRICTED


class FileSystemSandboxContext(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    """文件系统沙箱上下文（process/start 的 sandbox 参数的 wire 形态）"""

    permissions: ExecPermissionProfile = Field(default_factory=ExecPermissionProfile)
    cwd: str | None = None
    workspace_roots: list[str] = Field(default_factory=list, alias="workspaceRoots")
    user_home_dir: str | None = Field(default=None, alias="userHomeDir")
    temporary_directories: list[str] | None = Field(
        default=None, alias="temporaryDirectories"
    )
    windows_sandbox_level: WindowsSandboxLevel = Field(
        default=WindowsSandboxLevel.DISABLED, alias="windowsSandboxLevel"
    )
    windows_sandbox_private_desktop: bool = Field(
        default=False, alias="windowsSandboxPrivateDesktop"
    )
    windows_sandbox_proxy_settings_mode: WindowsSandboxProxySettingsMode | None = Field(
        default=None, alias="windowsSandboxProxySettingsMode"
    )
    use_legacy_landlock: bool = Field(default=False, alias="useLegacyLandlock")

    @classmethod
    def read_only(cls, cwd: str) -> "FileSystemSandboxContext":
        """cwd 及其内容只读（mac seatbelt / linux landlock+seccomp 生效）"""
        return cls(
            cwd=cwd,
            permissions=ExecPermissionProfile(
                type="managed",
                file_system=ExecManagedFileSystemPermissions(
                    type="restricted",
                    entries=[
                        ExecFileSystemSandboxEntry(
                            path=ExecFileSystemPath(type="path", path=_file_url(cwd)),
                            access=FileSystemAccessMode.READ,
                        )
                    ],
                ),
                network=NetworkSandboxPolicy.RESTRICTED,
            ),
        )

    @classmethod
    def workspace_write(
        cls,
        cwd: str,
        writable_roots: list[str] | None = None,
        *,
        network_enabled: bool = True,
    ) -> "FileSystemSandboxContext":
        """cwd 可写 + 额外可写根；网络默认放行"""
        roots = [cwd, *(writable_roots or [])]
        entries = [
            ExecFileSystemSandboxEntry(
                path=ExecFileSystemPath(type="path", path=_file_url(root)),
                access=FileSystemAccessMode.WRITE,
            )
            for root in roots
        ]
        return cls(
            cwd=cwd,
            permissions=ExecPermissionProfile(
                type="managed",
                file_system=ExecManagedFileSystemPermissions(
                    type="restricted", entries=entries
                ),
                network=(
                    NetworkSandboxPolicy.ENABLED
                    if network_enabled
                    else NetworkSandboxPolicy.RESTRICTED
                ),
            ),
        )


class ExecEnvPolicy(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    """子进程环境策略（wire：ExecEnvPolicy）"""

    inherit: str = "all"
    ignore_default_excludes: bool = Field(default=True, alias="ignoreDefaultExcludes")
    exclude: list[str] = Field(default_factory=list)
    set: dict[str, str] = Field(default_factory=dict)
    include_only: list[str] = Field(default_factory=list, alias="includeOnly")


class ShellSnapshotRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    scope_id: str = Field(..., alias="scopeId")
    shell: ShellInfo


class ManagedNetworkSandboxContext(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    """托管网络上下文（loopback 代理端口 + 本地绑定许可）"""

    loopback_ports: list[int] = Field(default_factory=list, alias="loopbackPorts")
    allow_local_binding: bool = Field(default=False, alias="allowLocalBinding")


def _file_url(path: str) -> str:
    """本地路径 → file:// URL（SDK 侧 PathUri wire 形态）"""
    if path.startswith("file://"):
        return path
    from pathlib import Path

    return Path(path).resolve().as_uri()


# =============================================================================
# 网络策略（托管网络沙箱——服务端反向请求裁决 + 审计通知）
# wire 形态对位 executor-protocol/src/network_policy.rs
# =============================================================================


class ExecServerNetworkProtocol(str, Enum):
    """网络协议标识（wire：snake_case）"""

    HTTP = "http"
    HTTPS_CONNECT = "https_connect"
    SOCKS5_TCP = "socks5_tcp"
    SOCKS5_UDP = "socks5_udp"


class ExecServerNetworkPolicyRequest(BaseModel):
    """一次网络访问尝试（network/policyRequest 的 request 字段）"""

    model_config = ConfigDict(populate_by_name=True)
    protocol: ExecServerNetworkProtocol
    host: str
    port: int


class NetworkPolicyRequestParams(BaseModel):
    """network/policyRequest 反向请求参数（服务端进程发起，客户端裁决）"""

    model_config = ConfigDict(populate_by_name=True)
    process_id: str = Field(..., alias="processId")
    request: ExecServerNetworkPolicyRequest


class NetworkPolicyDecision(BaseModel):
    """裁决结果（wire：internally-tagged type + reason——deny/ask 必带）"""

    model_config = ConfigDict(populate_by_name=True)
    type: Literal["allow", "deny", "ask"]
    reason: str | None = None

    @model_validator(mode="after")
    def _reason_required_for_deny_ask(self) -> "NetworkPolicyDecision":
        if self.type in ("deny", "ask") and not self.reason:
            raise ValueError(f"{self.type} 裁决必须携带 reason")
        return self

    @classmethod
    def allow(cls) -> "NetworkPolicyDecision":
        return cls(type="allow")

    @classmethod
    def deny(cls, reason: str) -> "NetworkPolicyDecision":
        return cls(type="deny", reason=reason)

    @classmethod
    def ask(cls, reason: str) -> "NetworkPolicyDecision":
        return cls(type="ask", reason=reason)


class NetworkPolicyRequestResponse(BaseModel):
    """network/policyRequest 的响应结果（结果只含 decision）"""

    model_config = ConfigDict(populate_by_name=True)
    decision: NetworkPolicyDecision
