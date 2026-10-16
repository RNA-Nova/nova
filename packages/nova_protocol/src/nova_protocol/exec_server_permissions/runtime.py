"""权限套餐运行时词汇（对位 codex `protocol/src/models.rs`，锚点
39f4277087）。

运行时主形状 `PermissionProfile`（models.rs:424 判别联合
Managed/Disabled/External）与 `ManagedFileSystemPermissions`
（models.rs:302 区域）**不重复落地**——其线上形状即
`exec_server_wire.ExecPermissionProfile` /
`ExecManagedFileSystemPermissions`（serde 形状逐字段同构，对照
packages/nova-exec-server/crates/exec-server-protocol-core/src/models.rs:276-412），
编译管线直接产出线上模型。本模块只收线上尚无的 sidecar 词汇。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..exec_server_wire import ExecFileSystemSandboxEntry

#: 内置只读套餐名（对位 models.rs:412）
BUILT_IN_PERMISSION_PROFILE_READ_ONLY = ":read-only"
#: 内置工作区写套餐名（对位 models.rs:415）
BUILT_IN_PERMISSION_PROFILE_WORKSPACE = ":workspace"
#: 内置全开放套餐名（对位 models.rs:418）
BUILT_IN_PERMISSION_PROFILE_DANGER_FULL_ACCESS = ":danger-full-access"


class ActivePermissionProfile(BaseModel):
    """活动套餐身份 sidecar（逐字段对位 models.rs:447-470——运行时以
    `ExecPermissionProfile` 为准，本对象只为客户端展示保留稳定身份）。

    serde 无 rename（snake_case 键原样——id/extends），不经 dump_wire。
    """

    #: 套餐 id（来自 `default_permissions` 或隐式内置缺省）
    id: str
    #: 选中套餐 `extends` 声明的父套餐 id（可缺席）
    extends: str | None = None

    @classmethod
    def read_only(cls) -> "ActivePermissionProfile":
        """对位 models.rs:466-470"""
        return cls(id=BUILT_IN_PERMISSION_PROFILE_READ_ONLY)


class AdditionalNetworkPermissions(BaseModel):
    """对位 `NetworkPermissions`（models.rs:250-253）"""

    enabled: bool | None = None

    def is_empty(self) -> bool:
        """对位 models.rs:255-258"""
        return self.enabled is None


class AdditionalFileSystemPermissions(BaseModel):
    """对位 `FileSystemPermissions`（models.rs:95-99）的现代正典形状。

    裁剪登记：codex 的 legacy `{read, write}` 根列表 serde 变体
    （`LegacyReadWriteRoots`，models.rs:102-107 + 自定义反序列化
    :221-245）属 app-server API 兼容面，随 request_permissions 流程批次
    落地，本批次不收。
    """

    entries: list[ExecFileSystemSandboxEntry] = Field(default_factory=list)
    glob_scan_max_depth: int | None = None

    def is_empty(self) -> bool:
        """对位 models.rs:111-114"""
        return not self.entries


class AdditionalPermissionProfile(BaseModel):
    """per-command 叠加的部分权限（逐字段对位 models.rs:263-266）"""

    network: AdditionalNetworkPermissions | None = None
    file_system: AdditionalFileSystemPermissions | None = None

    def is_empty(self) -> bool:
        """对位 models.rs:268-272"""
        return self.network is None and self.file_system is None
