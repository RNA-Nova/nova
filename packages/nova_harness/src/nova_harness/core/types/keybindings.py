"""快捷键相关类型定义。

本模块只声明快捷键配置与冲突诊断的纯数据类型。
业务函数（加载、合并、冲突检测）保留在 ``nova_harness.core.keybindings``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Union


@dataclass
class Keybinding:
    """单个快捷键定义。"""

    id: str
    default: Union[str, List[str]]
    description: Optional[str] = None
    reserved: bool = False


@dataclass
class KeybindingDiagnostic:
    """快捷键冲突诊断。"""

    type: str
    message: str
    shortcut: str
    extension_path: Optional[str] = None


__all__ = ["Keybinding", "KeybindingDiagnostic"]
