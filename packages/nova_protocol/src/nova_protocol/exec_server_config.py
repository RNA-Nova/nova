"""exec-server 客户端配置词汇 + 物化（批次 A——词汇归枢纽）。

从 nova-exec-server-client 迁入：`config.py` 的六个模型 + `policy.py` 的
物化逻辑。纪律（定案）：**套餐名与配置词汇永不上线**——上线的是展开后的
线上对象（`FileSystemSandboxContext` / `RemoteNetworkProxyLaunchConfig`），
executor 收到什么执行什么，不理解 nova 语义。

物化以 `to_*` 纯转换方法挂在 `ExecutorConfig` 上（枢纽"纯转换"通道）；
配置文件的**发现/读取/合并**（有 I/O）留在消费方（SDK 的 config loader），
不进本包。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Union

from pydantic import BaseModel, BeforeValidator, Field, PlainSerializer

from .base_model import NovaBaseModel
from .exec_server_permissions import PermissionsToml
from .exec_server_wire import (
    ExecFileSystemPath,
    FileSystemSandboxContext,
    NetworkDomainPermission,
    NetworkDomainPermissionEntry,
    NetworkDomainPermissions,
    NetworkMode,
    NetworkSandboxPolicy,
    RemoteNetworkProxyConfig,
    RemoteNetworkProxyLaunchConfig,
)

#: 端点 id 最大长度（对位 codex MAX_ENVIRONMENT_ID_LEN）——按 **UTF-8 字节**
#: 计量（对位 Rust `str::len()`；合法 id 字符集纯 ASCII，字节数恒等于字符数，
#: 字节口径只在同含非 ASCII 字符时影响先报哪条错）
MAX_ENVIRONMENT_ID_LENGTH = 64


class SandboxMode(str, Enum):
    """文件系统沙箱套餐名（配置词汇，永不上线——上线的是展开对象；
    对位 codex SandboxMode 三档）"""

    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    #: danger-full-access：显式不沙箱（物化为 None——与未配置的语义区分：
    #: 未配置 = executor 缺省姿态；本档 = 用户显式选择脱沙箱）
    DANGER_FULL_ACCESS = "danger-full-access"


class SandboxWorkspaceWriteConfig(BaseModel):
    """workspace-write 套餐微调旋钮（逐字段对位 codex SandboxWorkspaceWrite）"""

    writable_roots: list[str] = Field(default_factory=list)
    network_access: bool = False
    exclude_tmpdir_env_var: bool = False
    exclude_slash_tmp: bool = False


class NetworkProxySettings(BaseModel):
    """`[network_proxy]` 段（nova 自有词汇——codex config 无对应键（其托管
    网络由组织/云配置驱动）；字段形状照线上 RemoteNetworkProxyConfig 推导）"""

    enabled: bool = False
    #: 托管模式："proxy" = 经代理按名单放行；"none" = 无网络访问全拒
    mode: NetworkMode = NetworkMode.PROXY
    allowed_domains: list[str] = Field(default_factory=list)
    denied_domains: list[str] = Field(default_factory=list)


class ApprovalPolicy(str, Enum):
    """审批档字符串档（对位 codex AskForApproval 的 unit 变体）。

    granular 变体不是本枚举成员——见 `GranularApprovalConfig` 与
    `ApprovalPolicySetting` 联合（对位 codex AskForApproval::Granular）。
    untrusted 已被上游废弃不收（对位 codex config mod.rs:229 拒收语义，
    解析入口 `_parse_approval_policy` 给响亮报错）。
    """

    ON_REQUEST = "on-request"
    #: on-failure 是 codex OnRequest 的 serde alias（上游无独立变体）；
    #: 本成员为存量消费侧源码兼容保留，**配置解析不再产出**——原始字符串
    #: "on-failure" 一律落 ON_REQUEST（对位 serde alias 语义）
    ON_FAILURE = "on-failure"
    NEVER = "never"


class GranularApprovalConfig(NovaBaseModel):
    """granular 五开关审批负载（逐字段对位 codex protocol.rs:1054-1068
    `GranularApprovalConfig`）。

    某开关为 true = 该类审批请求放行（弹给用户）；false = 该类请求**自动
    拒绝**，不再上弹（对位 codex AskForApproval::Granular 文档注释）。
    """

    #: 是否放行 shell 命令审批请求（含 inline `with_additional_permissions`
    #: 与 `require_escalated` 请求）——必填（对位 codex sandbox_approval）
    sandbox_approval: bool
    #: 是否放行 execpolicy `prompt` 规则触发的审批——必填（对位 codex rules）
    rules: bool
    #: 是否放行 skill 脚本执行触发的审批——serde default=false（对位 codex
    #: skill_approval 的 #[serde(default)]）
    skill_approval: bool = False
    #: 是否放行 `request_permissions` 工具触发的审批——serde default=false
    #: （对位 codex request_permissions 的 #[serde(default)]）
    request_permissions: bool = False
    #: 是否放行 MCP elicitation 审批——必填（对位 codex mcp_elicitations）
    mcp_elicitations: bool

    # ── 访问器（逐一对位 codex protocol.rs:1070-1087 的 allows_* 方法）──

    def allows_sandbox_approval(self) -> bool:
        return self.sandbox_approval

    def allows_rules_approval(self) -> bool:
        return self.rules

    def allows_skill_approval(self) -> bool:
        return self.skill_approval

    def allows_request_permissions(self) -> bool:
        return self.request_permissions

    def allows_mcp_elicitations(self) -> bool:
        return self.mcp_elicitations


def _parse_approval_policy(value: object) -> object:
    """审批档反序列化预处理（对位 codex AskForApproval 的 serde 外部标签
    反序列化 + config mod.rs:3762-3767 的 untrusted 拒收）。

    - `ApprovalPolicy` 枚举成员原样透传（存量 Python 构造路径——含
      ON_FAILURE 直构，比对 `is`/`==` 的语义不变）；
    - 原始字符串 `"on-failure"` → ON_REQUEST（对位 serde
      `#[serde(alias = "on-failure")]`：alias 只影响反序列化方向）；
    - 原始字符串 `"untrusted"` → 响亮报错（文案对位 codex
      UnsupportedUntrustedApprovalPolicyError，config mod.rs:229）；
    - dict `{"granular": {...}}` → 解 serde 外部标签外壳，内层负载交
      GranularApprovalConfig 校验；裸负载 dict（无外壳）一并透传，由联合
      校验自然落 GranularApprovalConfig（Python 构造便利，serde 超集）。
    """
    if isinstance(value, ApprovalPolicy):
        return value
    if isinstance(value, str):
        if value == "untrusted":
            raise ValueError(
                'approval_policy = "untrusted" is no longer supported; '
                "remove this setting"
            )
        if value == ApprovalPolicy.ON_FAILURE.value:
            return ApprovalPolicy.ON_REQUEST
        return value
    if isinstance(value, dict) and set(value) == {"granular"}:
        return value["granular"]
    return value


def _dump_approval_policy(
    value: ApprovalPolicy | GranularApprovalConfig,
) -> object:
    """审批档序列化（逐字节对位 serde 外部标签）：unit 变体 → kebab-case
    字符串；granular → `{"granular": {...}}` 外壳，负载内字段 snake_case
    （codex 侧无 rename 属性，不用枢纽 camelCase 别名——不经 dump_wire）。"""
    if isinstance(value, ApprovalPolicy):
        return value.value
    return {"granular": value.model_dump()}


#: 审批档配置联合（对位 codex AskForApproval 全枚举——unit 三档 + granular
#: 负载）。判别按仓规 `union_mode="left_to_right"`：str→枚举、dict→
#: GranularApprovalConfig（serde 外部标签无显式判别键，str/dict 类型分野即
#: 判别，无 smart-union 猜测空间）。
ApprovalPolicySetting = Annotated[
    Union[ApprovalPolicy, GranularApprovalConfig],
    Field(union_mode="left_to_right"),
    BeforeValidator(_parse_approval_policy),
    PlainSerializer(_dump_approval_policy, return_type=object),
]


class ExecutorEnvironment(BaseModel):
    """`[[environments]]` 条目（逐字段对位 codex environments.toml 的
    EnvironmentToml——executor 环境注册表条目）。

    `url` 与 `program` 必须二选一（codex：must set exactly one of url or
    program）：url = WS 环境（ws:// 或 wss://）；program = stdio spawn
    命令（SSH 承载同款：`program = "ssh"`, `args = ["host", ...]`）。
    """

    id: str
    url: str | None = None
    program: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    #: 连接超时（秒；from_environment 接线为 connect 总时限）——仅 url
    #: 环境合法（校验期 `connect_timeout_sec requires url`，对位 codex
    #: environment_toml.rs）；与 initialize_timeout_sec 是两笔独立预算
    #: （codex：connect 管传输连接、initialize 管握手，缺省各 10s 互不挤占）
    connect_timeout_sec: float | None = None
    #: initialize 握手时限（秒；url/stdio 均合法——对位 codex 两种传输都
    #: 携带 initialize_timeout；经 ResolvedEnvironment 接线为握手预算）
    initialize_timeout_sec: float | None = None


@dataclass(frozen=True)
class ResolvedExecutionPolicy:
    """一次物化的完整结果（运行时值对象，frozen 锁死不可变）"""

    sandbox: FileSystemSandboxContext | None
    network_proxy: RemoteNetworkProxyLaunchConfig | None
    #: 透传配置档（字符串档或 granular 五开关——消费语义归消费侧批次）
    approval_policy: ApprovalPolicySetting


class ExecutorConfig(BaseModel):
    """合并后的有效 executor 配置（物化的输入）。

    词汇平铺对位 codex config.toml：`sandbox_mode` / `[sandbox_workspace_write]`
    / `approval_policy`（+ nova 自有的 `[network_proxy]`）；`[[environments]]`
    注册表对位 codex environments.toml（我们合并在同一 config.toml——层栈
    已定单文件）。project 层（`.nova/settings.json` 的 `exec-server` 段）内为
    同一词汇的 JSON 形态。
    """

    #: 沙箱套餐档（缺席 = 不物化、不下发——保持 nova 现状：未配置不沙箱，
    #: executor 按自身缺省姿态执行。注：codex 对 trusted 目录默认
    #: workspace-write——产品姿态差异，刻意不跟）
    sandbox_mode: SandboxMode | None = None
    sandbox_workspace_write: SandboxWorkspaceWriteConfig = Field(
        default_factory=SandboxWorkspaceWriteConfig
    )
    network_proxy: NetworkProxySettings | None = None
    #: 审批档（对位 codex config mod.rs:328 `approval_policy`——不收
    #: Constrained 托管约束包装，收裸 AskForApproval 语义）。字符串档三档 +
    #: granular 五开关负载（`{"granular": {...}}` 外壳对位 serde 外部标签）；
    #: 缺省 on-request（对位 codex AskForApproval 的 #[default] OnRequest）
    approval_policy: ApprovalPolicySetting = ApprovalPolicy.ON_REQUEST
    #: 默认权限套餐（A2b；逐字对位 codex ConfigToml `default_permissions`，
    #: config/src/config_toml.rs:236-239——`:` 前缀名指内置套餐
    #: （`:read-only`/`:workspace`/`:danger-full-access`），其余名字解析自
    #: `permissions` 表）
    default_permissions: str | None = None
    #: 命名权限套餐表（A2b；对位 codex ConfigToml `permissions`，
    #: config_toml.rs:241-243 的 `Option<PermissionsToml>`——flatten 的
    #: name→profile 映射，校验时收裸 dict）。解析/编译函数在
    #: `exec_server_permissions` 包（纯计算）；loader 接线归后续批次
    permissions: PermissionsToml | None = None
    #: 默认环境 id（对位 codex default；"none"（大小写不敏感）= 禁用默认；
    #: 缺席时按 include_local 落 local）
    default_environment: str | None = None
    #: 是否包含内建 local 环境（对位 codex include_local）
    include_local: bool = True
    environments: list[ExecutorEnvironment] = Field(default_factory=list)

    def to_file_system_sandbox(
        self, cwd: str | None
    ) -> FileSystemSandboxContext | None:
        """套餐档 → 展开的文件系统沙箱上下文（纯转换）。

        `sandbox_mode` 缺席或无 cwd → None（不下发，executor 按自身缺省姿态
        执行——保持 nova 现状：未配置不沙箱）。
        """
        mode = self.sandbox_mode
        if mode is None or not cwd:
            return None
        if mode is SandboxMode.DANGER_FULL_ACCESS:
            # 显式脱沙箱：不下发沙箱上下文
            return None
        if mode is SandboxMode.READ_ONLY:
            return FileSystemSandboxContext.read_only(cwd)

        knobs = self.sandbox_workspace_write
        context = FileSystemSandboxContext.workspace_write(
            cwd,
            writable_roots=knobs.writable_roots,
            network=(
                NetworkSandboxPolicy.ENABLED
                if knobs.network_access
                else NetworkSandboxPolicy.RESTRICTED
            ),
        )
        # exclude 旋钮（对位 codex：从可写条目里摘掉对应符号路径）
        excluded: list[ExecFileSystemPath] = []
        if knobs.exclude_slash_tmp:
            excluded.append(ExecFileSystemPath.slash_tmp())
        if knobs.exclude_tmpdir_env_var:
            excluded.append(ExecFileSystemPath.tmpdir())
        if excluded:
            entries = context.permissions.file_system.entries
            context.permissions.file_system.entries = [
                entry for entry in entries if entry.path not in excluded
            ]
        return context

    def to_network_proxy_launch(self) -> RemoteNetworkProxyLaunchConfig | None:
        """networkProxy 段 → 代理启动配置（纯转换；未配置/未启用 → None）。

        名单展开顺序：deny 条目在前（拒绝优先，防御性排序——服务端评估语义
        以 executor network-policy 为准）。
        """
        settings = self.network_proxy
        if settings is None or not settings.enabled:
            return None
        entries = [
            NetworkDomainPermissionEntry(
                domain=domain, permission=NetworkDomainPermission.DENY
            )
            for domain in settings.denied_domains
        ] + [
            NetworkDomainPermissionEntry(
                domain=domain, permission=NetworkDomainPermission.ALLOW
            )
            for domain in settings.allowed_domains
        ]
        return RemoteNetworkProxyLaunchConfig(
            proxy=RemoteNetworkProxyConfig(
                enabled=True,
                mode=settings.mode,
                domains=NetworkDomainPermissions(entries=entries) if entries else None,
            )
        )

    def resolve_execution(self, cwd: str | None) -> ResolvedExecutionPolicy:
        """物化单点：executor 配置 → 全量线上展开对象（纯转换）"""
        return ResolvedExecutionPolicy(
            sandbox=self.to_file_system_sandbox(cwd),
            network_proxy=self.to_network_proxy_launch(),
            approval_policy=self.approval_policy,
        )
