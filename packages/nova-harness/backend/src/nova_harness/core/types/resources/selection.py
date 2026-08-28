"""能力选配报告——yaml 名单点名项的裁决结果。

yaml 的选配是"请求"不是"命令"：点名的资源可能不存在（missing）、
被用户 settings 终裁（disabled_by_settings）或被宿主 SDK 闸拦下
（disabled_by_sdk）。散闸时代这些失败全部静默，单点裁决后每个名字
被哪一层拒掉天然可标记。

跨进程透出（RPC 快照）故用 NovaBaseModel——线上 camelCase。
"""

from typing import Literal

from nova_ai.types.base_model import NovaBaseModel

SelectionStatus = Literal[
    "ok",
    "missing",
    "disabled_by_settings",
    "disabled_by_sdk",
    # 预留：manifest 作者默认关（机制无消费者，见 resource-permission-refactor.md）
    # "disabled_by_manifest",
]


class CapabilitySelection(NovaBaseModel):
    """单个名单点名项的裁决结果。"""

    resource_type: str
    name: str
    status: SelectionStatus


__all__ = ["CapabilitySelection", "SelectionStatus"]
