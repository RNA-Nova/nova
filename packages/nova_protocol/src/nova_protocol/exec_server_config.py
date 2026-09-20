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

from pydantic import BaseModel, Field

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

#: 端点 id 最大长度（对位 codex MAX_ENVIRONMENT_ID_LEN）
MAX_ENVIRONMENT_ID_LENGTH = 64


class SandboxMode(str, Enum):
    """文件系统沙箱套餐名（配置词汇，永不上线——上线的是展开对象）"""

    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"


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
    """审批档（对位 codex AskForApproval；untrusted 已被上游废弃，不收）"""

    ON_REQUEST = "on-request"
    ON_FAILURE = "on-failure"
    NEVER = "never"


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
    #: 连接超时（秒；from_environment 接线为 connect 总时限）
    connect_timeout_sec: float | None = None
    initialize_timeout_sec: float | None = None


@dataclass(frozen=True)
class ResolvedExecutionPolicy:
    """一次物化的完整结果（运行时值对象，frozen 锁死不可变）"""

    sandbox: FileSystemSandboxContext | None
    network_proxy: RemoteNetworkProxyLaunchConfig | None
    approval_policy: ApprovalPolicy


class ExecutorConfig(BaseModel):
    """合并后的有效 executor 配置（物化的输入）。

    词汇平铺对位 codex config.toml：`sandbox_mode` / `[sandbox_workspace_write]`
    / `approval_policy`（+ nova 自有的 `[network_proxy]`）；`[[environments]]`
    注册表对位 codex environments.toml（我们合并在同一 config.toml——层栈
    已定单文件）。project 层（`.nova/settings.json` 的 `executor` 段）内为
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
    approval_policy: ApprovalPolicy = ApprovalPolicy.ON_REQUEST
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
