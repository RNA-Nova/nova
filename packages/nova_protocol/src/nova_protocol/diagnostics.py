"""装配诊断形状（``docs/crosscutting/diagnostics-modeling.md`` 诊断家族的批模式成员）。

``NovaDiagnostic`` 与 ``WarningEvent`` 是同一形状的两种节奏：流模式 =
运行时即发（``WarningEvent``——事件跟事件住 ``agent_events.py``）；批模式 =
装配期收集返回调用方、启动后批量转 WarningEvent 呈现（本模块）。

批模式的标准装置 ``DiagnosticsCollector`` 带行为（实时缝 + 日志），不住
纯形状枢纽——归 ``nova_ai/gateway/diagnostics.py``（词汇/装置分离：
枢纽只放形状，装置跟首个消费方走）。

住户注记：本类型是枢纽中罕见的非 Pydantic 形状（frozen dataclass）——
跨组件（nova_ai 产出 / harness 呈现）但当前不序列化，按数据建模规则 4/5
选型；将来若上过线（RPC 通知），升格 NovaBaseModel 是干净的 minor 演化。
"""

from dataclasses import dataclass
from typing import Literal

from .enums import WarningKind


@dataclass(frozen=True, kw_only=True)
class NovaDiagnostic:
    """装配诊断（不可变值对象——数据建模规则 5；纯运行态，不落盘不上线）。

    severity 三档（info/warning/error）× ``WarningKind`` 分类；装配层
    收集返回（不在装配层打印），由会话启动完成后统一呈现。
    """

    severity: Literal["info", "warning", "error"]
    kind: WarningKind
    message: str
