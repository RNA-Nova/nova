"""exec-server 线上词汇（nova-exec-server 协议的 Python 正典）。

从 nova-exec-server-client 迁入（批次 A——词汇归枢纽）。本模块是 PROTOCOL.md
的 Python 镜像：端点常量 + 全部 params/response/notification 模型，逐字段显式
alias 与 rust serde camelCase 形状互锁——因此刻意不继承 NovaBaseModel 的
alias_generator 通道（出厂配置面向 nova 内部词汇；本模块对齐外部协议金标）。
"""

from __future__ import annotations

import base64
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)

# =============================================================================
# 方法常量
# =============================================================================

#: 客户端协议版本（与服务端 InitializeResponse.protocol_version 做 major 匹配；
#: 跟随服务端 crates/exec-server-protocol/src/lib.rs::PROTOCOL_VERSION）
#: v1.13 = FileSystemSandboxContext 补可选 sandboxOverride（对位 codex 622e9e3696）
PROTOCOL_VERSION = "1.13"

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
FS_WRITE_BLOCK = "fs/writeBlock"
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

#: 流式写入单块解码后字节上限（对齐服务端 file_handle.rs 的
#: MAX_WRITE_STREAM_CHUNK_BYTES——v1.12 起随写流收编句柄族迁入；
#: 超限时服务端把流转入失败终态并在 done 报错）
MAX_WRITE_STREAM_CHUNK_BYTES = 4 * 1024 * 1024


# =============================================================================
# 基础类型
# =============================================================================


class JsonRpcRequest(BaseModel):
    """JSON-RPC 请求信封（id、方法名与可选参数）。"""

    model_config = ConfigDict(populate_by_name=True)
    id: int | None = None
    method: str
    params: dict[str, Any] | None = None


class JsonRpcResponse(BaseModel):
    """JSON-RPC 响应信封（结果与错误互斥由线上协议约束）。"""

    model_config = ConfigDict(populate_by_name=True)
    id: int | None = None
    result: Any | None = None
    error: JsonRpcError | None = None


class JsonRpcError(BaseModel):
    """JSON-RPC 错误对象（服务端错误码与消息）。"""

    model_config = ConfigDict(populate_by_name=True)
    code: int
    message: str


class JsonRpcNotification(BaseModel):
    """JSON-RPC 通知信封（无响应 id 的方法调用）。"""

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
            return base64.b64decode(v, validate=True)
        return v

    @model_serializer
    def _to_base64(self) -> str:
        """序列化为裸 base64 字符串（透明形态），非 {"data": ...} 包装对象"""
        return base64.b64encode(self.data).decode()


# =============================================================================
# 初始化
# =============================================================================


class InitializeParams(BaseModel):
    """initialize 请求（客户端身份与可选会话恢复 id）。"""

    model_config = ConfigDict(populate_by_name=True)
    client_name: str = Field(..., alias="clientName")
    #: 恢复既有会话（进程/文件句柄随会话存活）；None = 不下发，开新会话
    resume_session_id: str | None = Field(default=None, alias="resumeSessionId")


class InitializeResponse(BaseModel):
    """initialize 响应（会话 id、协议版本与可选环境快照）。"""

    model_config = ConfigDict(populate_by_name=True)
    session_id: str = Field(..., alias="sessionId")
    #: None = 旧服务端未报告协议版本，客户端跳过版本匹配。
    protocol_version: str | None = Field(default=None, alias="protocolVersion")
    #: 初始化捎带的执行端环境元数据（形状同 environment/info，省一次往返；
    #: 旧服务端无此字段 → None，客户端回退单次 environment/info 调用）
    environment_info: EnvironmentInfo | None = Field(
        default=None, alias="environmentInfo"
    )


class EnvironmentInfo(BaseModel):
    """执行端环境描述（shell、目录、平台与能力位）。"""

    model_config = ConfigDict(populate_by_name=True)
    shell: ShellInfo
    #: executor 发布版本（版本化兼容决策用；旧 executor 缺席时默认 "0.0.0"）
    executor_version: str = Field(default="0.0.0", alias="executorVersion")
    #: 不透明 executor 构建标识（行为验证查询键；旧版/未打标构建缺席。
    #: 非工件校验和、非安全证明，证据不得跨构建变体共享）
    provider_id: str | None = Field(default=None, alias="providerId")
    cwd: str | None = None
    user_home_dir: str | None = Field(default=None, alias="userHomeDir")
    platform_os: str | None = Field(default=None, alias="platformOs")
    #: 缺失时需前插到 PATH 的 executor 目录（按优先级序；缺席=空）
    prepend_path_dirs: list[str] = Field(default_factory=list, alias="prependPathDirs")
    temporary_directories: list[str] | None = Field(
        default=None, alias="temporaryDirectories"
    )
    temp_dir: str | None = Field(default=None, alias="tempDir")
    capabilities: EnvironmentCapabilities | None = None


class ShellInfo(BaseModel):
    """执行端默认 shell 的名称与可执行路径。"""

    model_config = ConfigDict(populate_by_name=True)
    name: str
    path: str


class EnvironmentCapabilities(BaseModel):
    """执行端能力位集合（缺席字段按不支持处理）。"""

    model_config = ConfigDict(populate_by_name=True)
    network_proxy_launch: bool = Field(default=False, alias="networkProxyLaunch")
    environment_config_read: bool = Field(default=False, alias="environmentConfigRead")
    sandboxed_file_streaming: bool = Field(
        default=False, alias="sandboxedFileStreaming"
    )
    #: v1.9：fs/open 支持 replace 模式且 fs/writeBlock 可用（对位 codex 同名位）
    file_write_streaming: bool = Field(default=False, alias="fileWriteStreaming")
    http_header_env_vars: bool = Field(default=False, alias="httpHeaderEnvVars")
    shell_snapshot_v2: bool = Field(default=False, alias="shellSnapshotV2")
    # v1.8：MXC Windows 沙箱可显式选择（对位 codex 同名位）
    windows_mxc: bool = Field(default=False, alias="windowsMxc")
    #: v1.10：Linux 根写保留标准设备（对位 codex 645b683a9e opt-in 位，false 省略）
    linux_root_write_preserves_devices: bool = Field(
        default=False, alias="linuxRootWritePreservesDevices"
    )
    #: v1.10：approved 根写保留设备与被拒根元数据符号链接目标（对位 codex 645b683a9e）
    linux_approved_root_write_preserves_restrictions: bool = Field(
        default=False, alias="linuxApprovedRootWritePreservesRestrictions"
    )


class EnvironmentStatus(BaseModel):
    """environment/status 响应（执行端就绪状态文本）。"""

    model_config = ConfigDict(populate_by_name=True)
    status: str


# ---------------------------------------------------------------------------
# environmentConfig/read（v1.4 起——executor 代读本机配置层栈，不合并不裁决）
# ---------------------------------------------------------------------------


class EnvironmentConfigReadParams(BaseModel):
    """代读请求：cwd 定位 project 层，config_paths 为键路径选择器

    （每条路径至少一个键段，如 [["sandbox"], ["network", "mode"]]；
    至少一条路径，否则服务端 invalid_params——不允许整文档读取）。
    """

    model_config = ConfigDict(populate_by_name=True)
    cwd: str
    config_paths: list[list[str]] = Field(..., alias="configPaths")


class EnvironmentConfigLayer(BaseModel):
    """一个已选的 executor 本机配置层。"""

    model_config = ConfigDict(populate_by_name=True)
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
    """有序配置层栈（从低到高优先级：user 层在前、project 层在后，两层恒在）。"""

    model_config = ConfigDict(populate_by_name=True)
    layers: list[EnvironmentConfigLayer]
    #: 预留对位字段（云托管层插入位）；nova 无云配置层，恒等于 len(layers)
    cloud_insertion_index: int = Field(..., alias="cloudInsertionIndex")


class EnvironmentConfigReadResponse(BaseModel):
    """executor 本机配置层栈与环境信息。"""

    model_config = ConfigDict(populate_by_name=True)
    #: executor 用户家目录（客户端展开 ~ 的目标）
    user_home_dir: str | None = Field(default=None, alias="userHomeDir")
    #: executor 家目录（~/.nova/exec-server，或 NOVA_EXEC_SERVER_HOME 覆盖）
    executor_home_dir: str = Field(..., alias="executorHomeDir")
    #: executor 主机名（诊断用途）
    hostname: str | None = None
    config: EnvironmentConfigLayerStack


# =============================================================================
# 进程管理
# =============================================================================


class ExecMetadata(BaseModel):
    """可选工具归因（executor 遥测用，非授权依据——旧客户端省略、旧 executor 忽略）"""

    model_config = ConfigDict(populate_by_name=True)
    thread_id: str | None = Field(default=None, alias="threadId")
    tool_call_id: str | None = Field(default=None, alias="toolCallId")


class ProcessStartParams(BaseModel):
    """process/start 请求（argv、执行环境与沙箱配置）。"""

    model_config = ConfigDict(populate_by_name=True)
    process_id: str = Field(..., alias="processId")
    #: 可选归因元数据（对位 ExecParams.metadata）
    metadata: ExecMetadata | None = None
    argv: list[str]
    cwd: str
    env: dict[str, str]
    tty: bool = False
    pipe_stdin: bool = Field(default=False, alias="pipeStdin")
    #: arg0 覆盖（真实 argv[0] 与可执行路径分离）
    arg0: str | None = Field(default=None, alias="arg0")
    #: 子进程环境策略（wire：ExecEnvPolicy）
    env_policy: ExecEnvPolicy | None = Field(default=None, alias="envPolicy")
    #: shell 快照请求（wire：ShellSnapshotRequest {scopeId, shell}）
    shell_snapshot: ShellSnapshotRequest | None = Field(
        default=None, alias="shellSnapshot"
    )
    #: 文件系统沙箱上下文（wire：FileSystemSandboxContext，编译后权限档形态）
    sandbox: FileSystemSandboxContext | None = None
    #: 托管网络强制（fail-closed：无 managedNetwork 细节时拒绝放行）
    enforce_managed_network: bool = Field(default=False, alias="enforceManagedNetwork")
    #: 托管网络细节（loopback 代理端口 + 本地绑定许可）
    managed_network: ManagedNetworkSandboxContext | None = Field(
        default=None, alias="managedNetwork"
    )
    #: executor 本地代理启动配置（wire：RemoteNetworkProxyLaunchConfig；
    #: 嵌套 dict 也会经模型校验——畸形配置尽早报错）
    network_proxy: RemoteNetworkProxyLaunchConfig | None = Field(
        default=None, alias="networkProxy"
    )


class ProcessStartResponse(BaseModel):
    """process/start 响应（进程 id 与实际沙箱类型）。"""

    model_config = ConfigDict(populate_by_name=True)
    process_id: str = Field(..., alias="processId")
    #: None = 对端未报告；none 则表示显式未使用沙箱。
    sandbox_type: (
        Literal[
            "none",
            "macosSeatbelt",
            "linuxSeccomp",
            "windowsRestrictedToken",
            "windowsMxc",
        ]
        | None
    ) = Field(default=None, alias="sandboxType")


class ProcessReadParams(BaseModel):
    """process/read 请求（增量游标、字节上限与等待时长）。"""

    model_config = ConfigDict(populate_by_name=True)
    process_id: str = Field(..., alias="processId")
    after_seq: int | None = Field(default=None, alias="afterSeq", ge=0)
    max_bytes: int | None = Field(default=None, alias="maxBytes", ge=0)
    wait_ms: int | None = Field(default=None, alias="waitMs", ge=0)


class ProcessOutputChunk(BaseModel):
    """一段带序号与流通道的 base64 进程输出。"""

    model_config = ConfigDict(populate_by_name=True)
    seq: int = Field(ge=0)
    stream: Literal["stdout", "stderr", "pty"]
    chunk: bytes

    @field_validator("chunk", mode="before")
    @classmethod
    def decode_chunk(cls, v: Any) -> bytes:
        if isinstance(v, str):
            return base64.b64decode(v, validate=True)
        return v

    @field_serializer("chunk")
    def encode_chunk(self, v: bytes) -> str:
        return base64.b64encode(v).decode()


class ProcessReadResponse(BaseModel):
    """process/read 响应（输出块、下一游标与进程终态）。"""

    model_config = ConfigDict(populate_by_name=True)
    chunks: list[ProcessOutputChunk]
    next_seq: int = Field(..., alias="nextSeq", ge=0)
    exited: bool
    exit_code: int | None = Field(default=None, alias="exitCode")
    closed: bool
    failure: str | None = None
    sandbox_denied: bool = Field(default=False, alias="sandboxDenied")


class ProcessWriteParams(BaseModel):
    """process/write 请求（向进程 stdin 写入一段字节）。"""

    model_config = ConfigDict(populate_by_name=True)
    process_id: str = Field(..., alias="processId")
    chunk: bytes
    write_id: str = Field(..., alias="writeId")

    @field_validator("chunk", mode="before")
    @classmethod
    def decode_chunk(cls, v: Any) -> bytes:
        if isinstance(v, str):
            return base64.b64decode(v, validate=True)
        return v

    @field_serializer("chunk")
    def serialize_chunk(self, v: bytes) -> str:
        """线上为 base64 字符串（Rust ByteChunk 透明序列化），否则 model_dump
        产出的 bytes 无法进 json.dumps"""
        return base64.b64encode(v).decode()


class ProcessWriteResponse(BaseModel):
    """process/write 响应（写入受理状态）。"""

    model_config = ConfigDict(populate_by_name=True)
    status: Literal["accepted", "unknownProcess", "stdinClosed", "starting"]


class ProcessSignalParams(BaseModel):
    """process/signal 请求（向指定进程发送信号）。"""

    model_config = ConfigDict(populate_by_name=True)
    process_id: str = Field(..., alias="processId")
    signal: Literal["interrupt"]


class ProcessTerminateParams(BaseModel):
    """process/terminate 请求（终止指定进程）。"""

    model_config = ConfigDict(populate_by_name=True)
    process_id: str = Field(..., alias="processId")


class ProcessTerminateResponse(BaseModel):
    """process/terminate 响应（终止后是否仍在运行）。"""

    model_config = ConfigDict(populate_by_name=True)
    running: bool


class ProcessExitedNotification(BaseModel):
    """process/exited 通知（退出码、序号与可选沙箱拒绝标记）。"""

    model_config = ConfigDict(populate_by_name=True)
    process_id: str = Field(..., alias="processId")
    seq: int
    exit_code: int = Field(..., alias="exitCode")
    # RS 端为 Option<bool>（三态：true/false/未报告——对端发显式 null 时容忍，
    # 对位 protocol.rs:1213 的 Option 形状；ProcessReadResponse 侧仍为必填 bool）
    sandbox_denied: bool | None = Field(default=None, alias="sandboxDenied")


class ProcessOutputDeltaNotification(BaseModel):
    """process/output 通知（对位 protocol.rs:1173 ExecOutputDeltaNotification）。

    SDK 侧经 process/read 轮询消费输出（有意），此模型备齐词汇不代表改推送。
    """

    model_config = ConfigDict(populate_by_name=True)
    process_id: str = Field(..., alias="processId")
    seq: int
    # RS 端为 ExecOutputStream 枚举（值集 "stdout"/"stderr"/"pty" 已对拍一致）
    stream: Literal["stdout", "stderr", "pty"]
    chunk: bytes

    @field_validator("chunk", mode="before")
    @classmethod
    def decode_chunk(cls, v: Any) -> bytes:
        if isinstance(v, str):
            return base64.b64decode(v, validate=True)
        return v

    @field_serializer("chunk")
    def encode_chunk(self, v: bytes) -> str:
        return base64.b64encode(v).decode()


class ProcessClosedNotification(BaseModel):
    """process/closed 通知（对位 protocol.rs:1192 ExecClosedNotification）。"""

    model_config = ConfigDict(populate_by_name=True)
    process_id: str = Field(..., alias="processId")
    seq: int


# =============================================================================
# 文件系统
# =============================================================================


class FsReadFileParams(BaseModel):
    """fs/readFile 请求（读取整个文件，可选符号链接与沙箱策略）。"""

    model_config = ConfigDict(populate_by_name=True)
    path: str
    #: 是否跟随符号链接；None = 不下发（服务端默认 true，保持旧行为）
    follow_symlinks: bool | None = Field(default=None, alias="followSymlinks")
    sandbox: FileSystemSandboxContext | None = None


class FsReadFileResponse(BaseModel):
    """fs/readFile 响应（完整文件内容的 base64 载体）。"""

    model_config = ConfigDict(populate_by_name=True)
    data_base64: str = Field(..., alias="dataBase64")

    @property
    def data(self) -> bytes:
        return base64.b64decode(self.data_base64, validate=True)


class FsOpenMode(str, Enum):
    """fs/open 打开模式（缺省 read——旧调用方保持只读打开语义）"""

    READ = "read"
    #: 写打开：文件缺失则创建、存在则截断
    REPLACE = "replace"


class FsOpenParams(BaseModel):
    """fs/open 请求（按只读或创建/截断模式打开文件句柄）。"""

    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")
    path: str
    #: 打开模式：read 读既有文件（缺省）；replace 写打开（创建/截断）
    mode: FsOpenMode = FsOpenMode.READ
    sandbox: FileSystemSandboxContext | None = None


class FsOpenResponse(BaseModel):
    """fs/open 响应（服务端确认的文件句柄 id）。"""

    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")


class FsReadBlockParams(BaseModel):
    """fs/readBlock 请求（从句柄的指定偏移读取定长字节）。"""

    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")
    offset: int = Field(ge=0)
    len: int = Field(ge=0)


class FsReadBlockResponse(BaseModel):
    """fs/readBlock 响应（字节块与文件结束标记）。"""

    model_config = ConfigDict(populate_by_name=True)
    chunk: bytes
    eof: bool

    @field_validator("chunk", mode="before")
    @classmethod
    def decode_chunk(cls, v: Any) -> bytes:
        if isinstance(v, str):
            return base64.b64decode(v, validate=True)
        return v

    @field_serializer("chunk")
    def encode_chunk(self, v: bytes) -> str:
        return base64.b64encode(v).decode()


class FsCloseParams(BaseModel):
    """fs/close 请求（关闭并释放文件句柄）。"""

    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")


class FsWriteBlockParams(BaseModel):
    """fs/writeBlock 请求（对位 RS `FsWriteBlockParams`）：显式 offset 定位写，
    chunk 为非空块、解码后 ≤1MiB（服务端 FILE_WRITE_CHUNK_SIZE 校验）"""

    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")
    offset: int
    #: 透明字节块——线上即裸 base64 字符串（同 ByteChunk）
    chunk: bytes

    @field_validator("chunk", mode="before")
    @classmethod
    def decode_chunk(cls, v: Any) -> bytes:
        if isinstance(v, str):
            return base64.b64decode(v, validate=True)
        return v

    @field_serializer("chunk")
    def encode_chunk(self, v: bytes) -> str:
        return base64.b64encode(v).decode()


class FsWriteBlockResponse(BaseModel):
    """成功响应即确认请求块的全部字节已落盘（对位 RS `FsWriteBlockResponse`）"""

    model_config = ConfigDict(populate_by_name=True)


class FsReadStreamParams(BaseModel):
    """fs/readStream 请求（打开服务端分片推送的读取流）。"""

    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")
    path: str
    offset: int = 0
    len: int | None = None
    block_size: int | None = Field(default=None, alias="blockSize")
    sandbox: FileSystemSandboxContext | None = None


class FsReadStreamResponse(BaseModel):
    """fs/readStream 响应（读取句柄与可选总字节数）。"""

    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")
    total_size: int | None = Field(default=None, alias="totalSize")


class FsReadStreamChunkNotification(BaseModel):
    """fs/readStream/chunk 通知（有序字节块与结束标记）。"""

    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")
    seq: int
    chunk: bytes
    eof: bool

    @field_validator("chunk", mode="before")
    @classmethod
    def decode_chunk(cls, v: Any) -> bytes:
        if isinstance(v, str):
            return base64.b64decode(v, validate=True)
        return v

    @field_serializer("chunk")
    def encode_chunk(self, v: bytes) -> str:
        return base64.b64encode(v).decode()


class FsReadStreamDoneNotification(BaseModel):
    """fs/readStream/done 通知（总读取字节数与可选错误）。"""

    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")
    total_bytes: int = Field(..., alias="totalBytes")
    error: str | None = None


# ---------------------------------------------------------------------------
# fs/writeStream（流式写入——readStream 的方向反转：客户端分片推，服务端顺序落盘）
# ---------------------------------------------------------------------------


class FsWriteStreamParams(BaseModel):
    """流式写入开句柄请求（缺省打开即创建/截断，与 fs/writeFile 语义一致）。"""

    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")
    path: str
    #: 断点续传起点（v1.12）：Some(n) = 不截断、从 n 续写（n 不得超当前文件
    #: 长度——不许隔洞写；文件不存在时 n>0 拒绝）；None = 创建/截断
    offset: int | None = None
    sandbox: FileSystemSandboxContext | None = None


class FsWriteStreamResponse(BaseModel):
    """fs/writeStream 响应（服务端接受的写入句柄 id）。"""

    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")


class FsWriteStreamChunkNotification(BaseModel):
    """流式写入数据块（客户端 → 服务端通知，无回执）。"""

    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")
    #: 从 0 开始的连续序号，服务端严格按序落盘（乱序记为流终态错误，
    #: 由随后的 done 回报——v1.12 起失败/中止不再删半成品）
    seq: int
    chunk: bytes
    #: 标记最后一个数据块；流仍须以 fs/writeStream/done 请求收尾确认
    eof: bool = False

    @field_validator("chunk", mode="before")
    @classmethod
    def decode_chunk(cls, v: Any) -> bytes:
        if isinstance(v, str):
            return base64.b64decode(v, validate=True)
        return v

    @field_serializer("chunk")
    def serialize_chunk(self, v: bytes) -> str:
        """线上为 base64 字符串（Rust ByteChunk 透明序列化）"""
        return base64.b64encode(v).decode()


class FsWriteStreamDoneParams(BaseModel):
    """fs/writeStream/done 请求（结束分片推送并请求最终确认）。"""

    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")


class FsWriteStreamDoneResponse(BaseModel):
    """fs/writeStream/done 响应（确认句柄与实际落盘字节数）。"""

    model_config = ConfigDict(populate_by_name=True)
    handle_id: str = Field(..., alias="handleId")
    #: 实际落盘的总字节数
    total_bytes: int = Field(..., alias="totalBytes")


class FsWriteFileParams(BaseModel):
    """fs/writeFile 请求（以完整 base64 内容创建或覆盖文件）。"""

    model_config = ConfigDict(populate_by_name=True)
    path: str
    data_base64: str = Field(..., alias="dataBase64")
    #: 是否跟随符号链接；None = 不下发（服务端默认 true，保持旧行为）
    follow_symlinks: bool | None = Field(default=None, alias="followSymlinks")
    sandbox: FileSystemSandboxContext | None = None


class FsCreateDirectoryParams(BaseModel):
    """fs/createDirectory 请求（创建目录，可选递归与沙箱策略）。"""

    model_config = ConfigDict(populate_by_name=True)
    path: str
    recursive: bool | None = None
    #: 是否跟随符号链接；None = 不下发（服务端默认 true，保持旧行为）
    follow_symlinks: bool | None = Field(default=None, alias="followSymlinks")
    sandbox: FileSystemSandboxContext | None = None


class FsGetMetadataParams(BaseModel):
    """fs/getMetadata 请求（读取文件类型、大小与时间戳）。"""

    model_config = ConfigDict(populate_by_name=True)
    path: str
    #: 是否跟随符号链接；None = 不下发（服务端默认 true，保持旧行为）
    follow_symlinks: bool | None = Field(default=None, alias="followSymlinks")
    sandbox: FileSystemSandboxContext | None = None


class FileMetadata(BaseModel):
    """文件元数据（类型标记、字节大小与毫秒时间戳）。"""

    model_config = ConfigDict(populate_by_name=True)
    is_directory: bool = Field(..., alias="isDirectory")
    is_file: bool = Field(..., alias="isFile")
    is_symlink: bool = Field(..., alias="isSymlink")
    size: int
    created_at_ms: int = Field(..., alias="createdAtMs")
    modified_at_ms: int = Field(..., alias="modifiedAtMs")


class FsCanonicalizeParams(BaseModel):
    """fs/canonicalize 请求（解析为执行端规范绝对路径）。"""

    model_config = ConfigDict(populate_by_name=True)
    path: str
    sandbox: FileSystemSandboxContext | None = None


class FsCanonicalizeResponse(BaseModel):
    """fs/canonicalize 响应（规范化后的路径）。"""

    model_config = ConfigDict(populate_by_name=True)
    path: str


class FsReadDirectoryParams(BaseModel):
    """fs/readDirectory 请求（列出目录的直接子项）。"""

    model_config = ConfigDict(populate_by_name=True)
    path: str
    sandbox: FileSystemSandboxContext | None = None


class DirEntry(BaseModel):
    """目录子项（文件名与文件/目录类型标记）。"""

    model_config = ConfigDict(populate_by_name=True)
    file_name: str = Field(..., alias="fileName")
    is_directory: bool = Field(..., alias="isDirectory")
    is_file: bool = Field(..., alias="isFile")


class FsReadDirectoryResponse(BaseModel):
    """fs/readDirectory 响应（目录直接子项列表）。"""

    model_config = ConfigDict(populate_by_name=True)
    entries: list[DirEntry]


class FsRemoveParams(BaseModel):
    """fs/remove 请求（删除文件或目录，可选递归与强制）。"""

    model_config = ConfigDict(populate_by_name=True)
    path: str
    recursive: bool | None = None
    force: bool | None = None
    #: 是否跟随符号链接；None = 不下发（服务端默认 true，保持旧行为）
    follow_symlinks: bool | None = Field(default=None, alias="followSymlinks")
    sandbox: FileSystemSandboxContext | None = None


class FsCopyParams(BaseModel):
    """fs/copy 请求（复制文件或目录树）。"""

    model_config = ConfigDict(populate_by_name=True)
    source_path: str = Field(..., alias="sourcePath")
    destination_path: str = Field(..., alias="destinationPath")
    recursive: bool
    sandbox: FileSystemSandboxContext | None = None


# ---------------------------------------------------------------------------
# fs/walk（目录遍历——Rust 侧已实现，SDK 补齐）
# ---------------------------------------------------------------------------


class WalkOptions(BaseModel):
    """遍历界限（对齐 executor-file-system WalkOptions；缺省值不超服务端
    上限 depth=64/directories=10000/entries=50000，超限整请求被拒）。"""

    model_config = ConfigDict(populate_by_name=True)
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
    """目录遍历条目（路径与 file/directory 类型）。"""

    model_config = ConfigDict(populate_by_name=True)
    path: str
    kind: Literal["file", "directory"]


class WalkError(BaseModel):
    """目录遍历错误（失败路径与诊断消息）。"""

    model_config = ConfigDict(populate_by_name=True)
    path: str
    message: str


class WalkOutcome(BaseModel):
    """目录遍历结果（条目、非致命错误与截断标记）。"""

    model_config = ConfigDict(populate_by_name=True)
    entries: list[WalkEntry] = []
    errors: list[WalkError] = []
    truncated: bool = False


class FsWalkParams(BaseModel):
    """fs/walk 请求（按界限遍历目录并收集条目与错误）。"""

    model_config = ConfigDict(populate_by_name=True)
    path: str
    options: WalkOptions
    sandbox: FileSystemSandboxContext | None = None


# =============================================================================
# http/request（executor 代发 HTTP）
# =============================================================================

HTTP_REQUEST = "http/request"
HTTP_REQUEST_BODY_DELTA = "http/request/bodyDelta"


class HttpRedirectPolicy(str, Enum):
    """HTTP 重定向策略（跟随或由执行端停止）。"""

    FOLLOW = "follow"
    STOP = "stop"


class HttpHeader(BaseModel):
    """HTTP 头（value_env_var：由执行端环境变量填值——凭据不跨线委派）"""

    model_config = ConfigDict(populate_by_name=True)
    name: str
    value: str
    value_env_var: str | None = Field(default=None, alias="valueEnvVar")


class HttpRequestParams(BaseModel):
    """http/request 请求（方法、URL、头、正文与传输策略）。"""

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
    """http/request 非流式响应（状态码、响应头与完整正文）。"""

    model_config = ConfigDict(populate_by_name=True)
    status: int
    headers: list[HttpHeader] = Field(default_factory=list)
    body: ByteChunk = Field(alias="bodyBase64")


class HttpRequestBodyDeltaNotification(BaseModel):
    """http/request/bodyDelta 通知（有序响应正文块与终态）。"""

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
    """网络沙箱策略（受限拦截或显式启用网络）。"""

    RESTRICTED = "restricted"
    ENABLED = "enabled"


class WindowsSandboxLevel(str, Enum):
    """windowsSandboxLevel 线上枚举（字段名保留 legacy 名兼容；
    mxc 是独立沙箱实现而非 RestrictedToken 档位——对位上游注释）"""

    DISABLED = "disabled"
    RESTRICTED_TOKEN = "restricted-token"
    ELEVATED = "elevated"
    MXC = "mxc"


class FileSystemAccessMode(str, Enum):
    """文件系统条目的读、写或拒绝访问模式。"""

    READ = "read"
    WRITE = "write"
    DENY = "deny"


class WindowsSandboxProxySettingsMode(str, Enum):
    """Windows 沙箱代理设置的处理模式（协调或保留）。"""

    RECONCILE = "reconcile"
    PRESERVE = "preserve"


class SandboxOverride(str, Enum):
    """sandboxOverride 线上枚举（v1.13，对位 codex 622e9e3696 protocol/sandbox.rs：
    控制面选定的沙箱覆盖决策，camelCase 值；仅观测随行，不放宽权限）"""

    NO_OVERRIDE = "noOverride"
    ESCALATED_SANDBOX_WITH_RESTRICTIONS = "escalatedSandboxWithRestrictions"
    BYPASS_SANDBOX_FIRST_ATTEMPT = "bypassSandboxFirstAttempt"


_SpecialPathKind = Literal[
    "root", "minimal", "project_roots", "tmpdir", "slash_tmp", "unknown"
]


class ExecFileSystemSpecialPath(BaseModel):
    """沙箱符号路径（wire：tag "kind"，snake_case——对位 executor-protocol-core
    FileSystemSpecialPath；执法时由服务端按 cwd 解析，不进套餐展开的具体路径）"""

    model_config = ConfigDict(populate_by_name=True)
    kind: _SpecialPathKind
    subpath: str | None = None
    #: kind=unknown 时的原始 token（前向兼容——新配置的旧运行时降级而非拒载）
    path: str | None = None


class ExecFileSystemPath(BaseModel):
    """沙箱条目路径（wire：internally-tagged "type"）

    type=path → `path`（file:// URI）；type=glob_pattern → `pattern`（仅 deny
    语义）；type=special → `value`（符号路径，执法时解析）。
    """

    model_config = ConfigDict(populate_by_name=True)
    type: Literal["path", "glob_pattern", "special"]
    path: str | None = None
    pattern: str | None = None
    value: ExecFileSystemSpecialPath | None = None

    @model_validator(mode="after")
    def _variant_field_required(self) -> "ExecFileSystemPath":
        required = {"path": "path", "glob_pattern": "pattern", "special": "value"}[
            self.type
        ]
        if getattr(self, required) is None:
            raise ValueError(f"type={self.type} 需要 {required} 字段")
        return self

    @classmethod
    def of_path(cls, path: str) -> "ExecFileSystemPath":
        """具体路径（自动归一为 file:// URI）"""
        return cls(type="path", path=_file_url(path))

    @classmethod
    def of_special(
        cls, kind: _SpecialPathKind, subpath: str | None = None
    ) -> "ExecFileSystemPath":
        """构造服务端按执法上下文解析的符号路径。"""
        return cls(
            type="special", value=ExecFileSystemSpecialPath(kind=kind, subpath=subpath)
        )

    @classmethod
    def root(cls) -> "ExecFileSystemPath":
        return cls.of_special("root")

    @classmethod
    def project_roots(cls, subpath: str | None = None) -> "ExecFileSystemPath":
        return cls.of_special("project_roots", subpath)

    @classmethod
    def tmpdir(cls) -> "ExecFileSystemPath":
        return cls.of_special("tmpdir")

    @classmethod
    def slash_tmp(cls) -> "ExecFileSystemPath":
        return cls.of_special("slash_tmp")

    @classmethod
    def glob(cls, pattern: str) -> "ExecFileSystemPath":
        return cls(type="glob_pattern", pattern=pattern)


class ExecFileSystemSandboxEntry(BaseModel):
    """一条文件系统沙箱规则（目标路径与访问模式）。"""

    model_config = ConfigDict(populate_by_name=True)
    path: ExecFileSystemPath
    access: FileSystemAccessMode
    #: 路径不存在时的行为（wire 现仅 "skip"）
    # 线上键即 snake_case（codex 原版无 rename——py 曾误配 camelCase alias
    # 导致带沙箱载荷在 RS 端反序列化失败，二轮审计抓出）
    missing_path_behavior: Literal["skip"] | None = None


class ExecRestrictedFileSystemPermissions(BaseModel):
    """托管文件系统受限变体（按 entries 逐条执法）。"""

    model_config = ConfigDict(populate_by_name=True)
    type: Literal["restricted"] = "restricted"
    entries: list[ExecFileSystemSandboxEntry] = Field(default_factory=list)
    # 线上键 snake_case；None = 未声明 glob 扫描深度限制。
    glob_scan_max_depth: int | None = Field(default=None, gt=0)


class ExecUnrestrictedFileSystemPermissions(BaseModel):
    """托管文件系统不受限变体（unit variant，不携带 entries）。"""

    model_config = ConfigDict(populate_by_name=True)
    type: Literal["unrestricted"] = "unrestricted"


ExecManagedFileSystemPermissions = Annotated[
    ExecRestrictedFileSystemPermissions | ExecUnrestrictedFileSystemPermissions,
    Field(discriminator="type"),
]
"""托管文件系统权限的显式判别联合。"""


class ExecManagedPermissionProfile(BaseModel):
    """由 executor 构造文件系统与网络沙箱的 managed 权限套餐。"""

    model_config = ConfigDict(populate_by_name=True)
    type: Literal["managed"] = "managed"
    # 线上键 snake_case `file_system`（与 Rust serde 正典一致）。
    file_system: ExecManagedFileSystemPermissions = Field(
        default_factory=ExecRestrictedFileSystemPermissions,
    )
    network: NetworkSandboxPolicy = NetworkSandboxPolicy.RESTRICTED


class ExecDisabledPermissionProfile(BaseModel):
    """不施加外层沙箱的 disabled 权限套餐（unit variant）。"""

    model_config = ConfigDict(populate_by_name=True)
    type: Literal["disabled"] = "disabled"


class ExecExternalPermissionProfile(BaseModel):
    """由外部调用方负责文件系统隔离的 external 权限套餐。"""

    model_config = ConfigDict(populate_by_name=True)
    type: Literal["external"] = "external"
    network: NetworkSandboxPolicy = NetworkSandboxPolicy.RESTRICTED


ExecPermissionProfile = Annotated[
    ExecManagedPermissionProfile
    | ExecDisabledPermissionProfile
    | ExecExternalPermissionProfile,
    Field(discriminator="type"),
]
"""执行权限套餐的显式判别联合。"""


class WireFileSystemPolicyContext(BaseModel):
    """policyContext 子对象（v1.11 起策略目录的唯一线上承载，对位 codex
    841b5490b2 的 WireFileSystemPolicyContext 终态化）"""

    model_config = ConfigDict(populate_by_name=True)
    cwd: str | None = None
    workspace_roots: list[str] = Field(default_factory=list, alias="workspaceRoots")


class FileSystemSandboxContext(BaseModel):
    """文件系统沙箱上下文（process/start 的 sandbox 参数的 wire 形态）。

    v1.11 终态形状：策略目录只经 `policyContext` 承载——codex 为老客户端
    保留的平铺 `cwd`/`workspaceRoots` 双形状字段已删（nova 从未对外发布，
    没有老客户端存在）。客户端整个省略 policyContext 时 executor 入口
    回退为自身 cwd（人机工学，保留）。
    """

    model_config = ConfigDict(populate_by_name=True)

    @model_serializer(mode="wrap")
    def _serialize_without_none(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        """嵌套进其他请求模型时也沿用同一 absent 语义，不做双道 dump。"""

        def drop_none(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: drop_none(item)
                    for key, item in value.items()
                    if item is not None
                }
            if isinstance(value, list):
                return [drop_none(item) for item in value]
            return value

        return drop_none(handler(self))

    permissions: ExecPermissionProfile = Field(
        default_factory=ExecManagedPermissionProfile
    )
    #: 控制面选定的沙箱覆盖决策（v1.13，对位 codex 622e9e3696 线上顶层字段——
    #: 不进 policyContext）。None ≡ RS 缺省 NoOverride：由本模型序列化器省略，
    #: 对位 RS 的 skip_serializing_if = "SandboxOverride::is_no_override"；
    #: 显式 NO_OVERRIDE 与 None 在线上同义（服务端反序列化缺省即 NoOverride）。
    sandbox_override: SandboxOverride | None = Field(
        default=None, alias="sandboxOverride"
    )
    #: 策略目录（cwd + workspaceRoots）。省略 = executor 入口回退自身 cwd
    policy_context: WireFileSystemPolicyContext | None = Field(
        default=None, alias="policyContext"
    )
    user_home_dir: str | None = Field(default=None, alias="userHomeDir")
    temporary_directories: list[str] | None = Field(
        default=None, alias="temporaryDirectories"
    )
    windows_sandbox_level: WindowsSandboxLevel = Field(
        default=WindowsSandboxLevel.DISABLED, alias="windowsSandboxLevel"
    )
    windows_sandbox_proxy_settings_mode: WindowsSandboxProxySettingsMode | None = Field(
        default=None, alias="windowsSandboxProxySettingsMode"
    )
    use_legacy_landlock: bool = Field(default=False, alias="useLegacyLandlock")

    @classmethod
    def _policy_context(cls, cwd: str | None) -> WireFileSystemPolicyContext | None:
        """RS `from_permission_profile` 对位：cwd 给定时 policyContext 承载
        cwd 与 workspace_roots=[cwd]；cwd 缺省 = 省略（入口回退）。
        cwd 走 `_file_url` 归一化——线上形状是 file:// URI，原生路径会被
        服务端 `PathUri` 反序列化拒绝（与 writable_roots 同一路径归一）。"""
        if cwd is None:
            return None
        cwd_url = _file_url(cwd)
        return WireFileSystemPolicyContext(cwd=cwd_url, workspaceRoots=[cwd_url])

    @classmethod
    def read_only(cls, cwd: str | None = None) -> "FileSystemSandboxContext":
        """codex `:read-only` 套餐逐字段对位：全盘可读、无处可写、网络受限。

        `cwd` 只作执法上下文（服务端符号解析的基准）——可读范围走符号
        `:root`，不把路径烤进条目。
        """
        return cls(
            policyContext=cls._policy_context(cwd),
            permissions=ExecManagedPermissionProfile(
                type="managed",
                file_system=ExecRestrictedFileSystemPermissions(
                    type="restricted",
                    entries=[
                        ExecFileSystemSandboxEntry(
                            path=ExecFileSystemPath.root(),
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
        cwd: str | None = None,
        writable_roots: list[str] | None = None,
        *,
        network: NetworkSandboxPolicy = NetworkSandboxPolicy.RESTRICTED,
    ) -> "FileSystemSandboxContext":
        """codex `:workspace`（workspace-write）套餐逐字段对位：

        全盘只读基座 + 项目根可写（符号 `:project_roots`，执法时按服务端
        cwd 解析——远程场景即 remote_cwd）+ /tmp 与 $TMPDIR 可写 + 用户
        附加根可写 + 项目根下 `.git`/`.nova` 降只读（元数据保护，对位
        codex 的 .git/.agents/.codex）；**网络默认受限**（放行归
        network_proxy 名单，不靠档位默认开）。
        """
        entries = [
            ExecFileSystemSandboxEntry(
                path=ExecFileSystemPath.root(), access=FileSystemAccessMode.READ
            ),
            ExecFileSystemSandboxEntry(
                path=ExecFileSystemPath.project_roots(),
                access=FileSystemAccessMode.WRITE,
            ),
            ExecFileSystemSandboxEntry(
                path=ExecFileSystemPath.slash_tmp(), access=FileSystemAccessMode.WRITE
            ),
            ExecFileSystemSandboxEntry(
                path=ExecFileSystemPath.tmpdir(), access=FileSystemAccessMode.WRITE
            ),
        ]
        entries.extend(
            ExecFileSystemSandboxEntry(
                path=ExecFileSystemPath.of_path(root), access=FileSystemAccessMode.WRITE
            )
            for root in (writable_roots or [])
        )
        # 元数据保护：项目根下的 .git/.nova 在可写基座上降只读
        entries.extend(
            ExecFileSystemSandboxEntry(
                path=ExecFileSystemPath.project_roots(name),
                access=FileSystemAccessMode.READ,
            )
            for name in (".git", ".nova")
        )
        return cls(
            policyContext=cls._policy_context(cwd),
            permissions=ExecManagedPermissionProfile(
                type="managed",
                file_system=ExecRestrictedFileSystemPermissions(
                    type="restricted", entries=entries
                ),
                network=network,
            ),
        )


class ExecEnvPolicy(BaseModel):
    """子进程环境策略（继承、排除、显式设置与白名单）。"""

    model_config = ConfigDict(populate_by_name=True)
    inherit: str = "all"
    ignore_default_excludes: bool = Field(default=True, alias="ignoreDefaultExcludes")
    exclude: list[str] = Field(default_factory=list)
    set: dict[str, str] = Field(default_factory=dict)
    include_only: list[str] = Field(default_factory=list, alias="includeOnly")


class ShellSnapshotRequest(BaseModel):
    """shell 快照请求（作用域 id 与待快照的 shell）。"""

    model_config = ConfigDict(populate_by_name=True)
    scope_id: str = Field(..., alias="scopeId")
    shell: ShellInfo


class ManagedNetworkSandboxContext(BaseModel):
    """托管网络上下文（loopback 代理端口 + 本地绑定许可 + Unix socket 许可）。"""

    model_config = ConfigDict(populate_by_name=True)
    loopback_ports: list[int] = Field(default_factory=list, alias="loopbackPorts")
    allow_local_binding: bool = Field(default=False, alias="allowLocalBinding")
    #: 有效托管网络策略放行的 Unix socket 路径
    allow_unix_sockets: list[str] = Field(
        default_factory=list, alias="allowUnixSockets"
    )
    #: 有效策略是否放行全部 Unix socket 连接（危险开关，默认关）
    dangerously_allow_all_unix_sockets: bool = Field(
        default=False, alias="dangerouslyAllowAllUnixSockets"
    )


def _file_url(path: str) -> str:
    """POSIX/Windows 原生绝对路径 → file:// URL（SDK PathUri wire 形态）。

    纯字符串构造（枢纽纪律：禁 pathlib/urllib）。支持 POSIX、驱动器与 UNC；
    仅对 URL path 保留集之外的字符做百分号编码。
    """
    if path.startswith("file://"):
        return path
    normalized = path.replace("\\", "/")
    if normalized.startswith("//"):
        # UNC 的 server 是 URL host，不能保留成路径开头的双斜杠。
        normalized = normalized[2:]
    elif (
        len(normalized) >= 3
        and normalized[0].isascii()
        and normalized[0].isalpha()
        and normalized[1] == ":"
        and normalized[2] == "/"
    ):
        normalized = "/" + normalized
    elif not normalized.startswith("/"):
        raise ValueError(f"路径须为绝对路径：{path}")
    safe = "/:@-._~!$&'()*+,;="
    encoded = "".join(
        ch if (ch.isascii() and (ch.isalnum() or ch in safe)) else f"%{b:02X}"
        for ch in normalized
        for b in ch.encode("utf-8")
    )
    return "file://" + encoded


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
    #: 对位 network_policy.rs 的 u16——越界即线上载荷解析失败（-32602 分支）
    port: int = Field(ge=0, le=65535)


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


class NetworkPolicyDecisionNotification(BaseModel):
    """network/policyDecision 审计通知（裁决后的单向汇报，丢只影响审计完整性）

    wire 形态对位 executor-protocol/src/network_policy.rs
    NetworkPolicyDecisionNotification（camelCase；method/client 可缺席，
    policyOverride 缺省 false）。
    """

    model_config = ConfigDict(populate_by_name=True)
    process_id: str = Field(..., alias="processId")
    timestamp: str
    scope: str
    decision: str
    source: str
    reason: str
    protocol: ExecServerNetworkProtocol
    host: str
    port: int
    method: str | None = None
    client: str | None = None
    policy_override: bool = Field(default=False, alias="policyOverride")


# ---------------------------------------------------------------------------
# 托管网络代理启动配置（process/start 的 networkProxy 参数 wire 形态，
# 对位 executor-network-proxy/src/lib.rs 的 RemoteNetworkProxyLaunchConfig 族）
# ---------------------------------------------------------------------------


class NetworkMode(str, Enum):
    """托管网络模式（wire：camelCase——对位 rust NetworkMode）"""

    NONE = "none"
    PROXY = "proxy"


class NetworkDomainPermission(str, Enum):
    """网络目标的允许或拒绝决策。"""

    ALLOW = "allow"
    DENY = "deny"


class NetworkDomainPermissionEntry(BaseModel):
    """一条域名权限规则（域名与允许/拒绝决策）。"""

    model_config = ConfigDict(populate_by_name=True)
    domain: str
    permission: NetworkDomainPermission


class NetworkDomainPermissions(BaseModel):
    """域名权限规则列表。"""

    model_config = ConfigDict(populate_by_name=True)
    entries: list[NetworkDomainPermissionEntry] = Field(default_factory=list)


class NetworkUnixSocketPermissionEntry(BaseModel):
    """一条 Unix socket 路径权限规则。"""

    model_config = ConfigDict(populate_by_name=True)
    path: str
    permission: NetworkDomainPermission


class NetworkUnixSocketPermissions(BaseModel):
    """Unix socket 路径权限规则列表。"""

    model_config = ConfigDict(populate_by_name=True)
    entries: list[NetworkUnixSocketPermissionEntry] = Field(default_factory=list)


class NetworkProxyAuditMetadata(BaseModel):
    """审计元数据（随 policyDecision 通知原样回显；全字段可缺省）"""

    model_config = ConfigDict(populate_by_name=True)
    conversation_id: str | None = Field(default=None, alias="conversationId")
    turn_id: str | None = Field(default=None, alias="turnId")


class RemoteNetworkProxyConfig(BaseModel):
    """代理本体配置（wire：RemoteNetworkProxyConfig）"""

    model_config = ConfigDict(populate_by_name=True)
    enabled: bool = False
    enable_socks5: bool = Field(default=False, alias="enableSocks5")
    enable_socks5_udp: bool = Field(default=False, alias="enableSocks5Udp")
    allow_upstream_proxy: bool = Field(default=False, alias="allowUpstreamProxy")
    dangerously_allow_all_unix_sockets: bool = Field(
        default=False, alias="dangerouslyAllowAllUnixSockets"
    )
    mode: NetworkMode = NetworkMode.NONE
    #: None = 未配置域名名单，不主动放行任何域名。
    domains: NetworkDomainPermissions | None = None
    #: None = 未配置 Unix socket 名单，不主动放行任何 socket。
    unix_sockets: NetworkUnixSocketPermissions | None = Field(
        default=None, alias="unixSockets"
    )
    allow_local_binding: bool = Field(default=False, alias="allowLocalBinding")


class RemoteNetworkProxyLaunchConfig(BaseModel):
    """process/start 的 networkProxy 参数（wire：RemoteNetworkProxyLaunchConfig）"""

    model_config = ConfigDict(populate_by_name=True)
    proxy: RemoteNetworkProxyConfig
    audit_metadata: NetworkProxyAuditMetadata = Field(
        default_factory=NetworkProxyAuditMetadata, alias="auditMetadata"
    )
    environment_id: str | None = Field(default=None, alias="environmentId")
    execution_id: str | None = Field(default=None, alias="executionId")
    policy_decision_timeout_ms: int | None = Field(
        default=None, alias="policyDecisionTimeoutMs"
    )
