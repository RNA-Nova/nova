"""物化层：executor 配置 → 线上展开对象。

纪律（定案）：**套餐名与配置词汇永不上线**——上线的是展开后的协议对象
（`FileSystemSandboxContext` / `RemoteNetworkProxyLaunchConfig`）。
executor 收到什么执行什么，不理解 nova 语义（纯执行后端）。

本模块取代 bundle 侧旧件 `nova_coding_agent/executor/policy.py`（批次 3
收编接线后退役）——裁决词汇下沉 executor 栈后的物化单点。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .config import ApprovalPolicy, ExecutorConfig, SandboxMode
from .protocol import (
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


def resolve_file_system_sandbox(
    config: ExecutorConfig, cwd: str | None
) -> FileSystemSandboxContext | None:
    """套餐档 → 展开的文件系统沙箱上下文。

    `sandbox_mode` 缺席或无 cwd → None（不下发，executor 按自身缺省姿态
    执行——保持 nova 现状：未配置不沙箱）。
    """
    mode = config.sandbox_mode
    if mode is None or not cwd:
        return None
    if mode is SandboxMode.READ_ONLY:
        return FileSystemSandboxContext.read_only(cwd)

    knobs = config.sandbox_workspace_write
    context = FileSystemSandboxContext.workspace_write(
        cwd,
        writable_roots=knobs.writable_roots,
        network=(
            NetworkSandboxPolicy.ENABLED
            if knobs.network_access
            else NetworkSandboxPolicy.RESTRICTED
        ),
    )
    # exclude 旋钮（对位 codex：从可写条目里摘掉对应符号路径——codex 经
    # PermissionProfile::workspace_write_with 的同名旗标实现，语义一致）
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


def resolve_network_proxy(
    config: ExecutorConfig,
) -> RemoteNetworkProxyLaunchConfig | None:
    """networkProxy 段 → 代理启动配置（未配置/未启用 → None，不下发）。

    名单展开顺序：deny 条目在前（拒绝优先，防御性排序——服务端评估语义
    以 executor network-policy 为准）。
    """
    settings = config.network_proxy
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


def resolve_ask_behavior(
    policy: ApprovalPolicy, *, ui_available: bool
) -> Literal["ask", "deny"]:
    """ask 类中间态的最终行为（fail-closed）：

    - `approval_policy = "never"` → ask 降级为 deny（对位 codex：
      prompt 在 approval_policy=never 下视为拒绝）；
    - 无 UI（headless/RPC 无交互能力）→ ask 无法完成，同样降级 deny。
    """
    if policy is ApprovalPolicy.NEVER or not ui_available:
        return "deny"
    return "ask"


@dataclass(frozen=True)
class ResolvedExecutionPolicy:
    """一次物化的完整结果（运行时值对象，frozen 锁死不可变）"""

    sandbox: FileSystemSandboxContext | None
    network_proxy: RemoteNetworkProxyLaunchConfig | None
    approval_policy: ApprovalPolicy


def resolve_execution_policy(
    config: ExecutorConfig, cwd: str | None
) -> ResolvedExecutionPolicy:
    """物化单点：executor 配置 → 全量线上展开对象"""
    return ResolvedExecutionPolicy(
        sandbox=resolve_file_system_sandbox(config, cwd),
        network_proxy=resolve_network_proxy(config),
        approval_policy=config.approval_policy,
    )
