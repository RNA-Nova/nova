"""UI 能力抽象类型统一入口。"""

from nova_harness.core.types.ui.context import UIContext
from nova_harness.core.types.ui.primitives import ExtensionMode, UIResponse

__all__ = ["ExtensionMode", "UIContext", "UIResponse"]
