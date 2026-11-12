"""诊断收集器（``DiagnosticsCollector``）——批模式标准装置。

``NovaDiagnostic`` 的容器 + 实时缝：所有"警告即数据"场景的统一装置
（词汇住 ``nova_protocol/diagnostics.py``，词汇/装置分离——枢纽只放
纯形状，装置带行为故随首个消费方住本网关层；harness 装配路径复用时
经 ``nova_ai`` 导入即可）。

纪律与语义见 ``docs/crosscutting/diagnostics-modeling.md`` §五。
"""

import logging
from typing import Callable, Iterator, List, Optional

from nova_protocol import NovaDiagnostic

logger = logging.getLogger(__name__)


class DiagnosticsCollector:
    """诊断收集器（``NovaDiagnostic`` 的容器 + 实时缝）。

    双通道职责：

    - ``items``：**无条件收集**——归属本次操作、随结果返回、无听众不丢
      （headless / 槽位空缺时警告依然存活）；
    - ``on_diagnostic``：**可选槽位**——append 时同步外发（实时呈现）。
      组合根在装配期注入（如 harness 桥接成 ``WarningEvent`` 上总线）；
      缺省 None 即退化为纯批模式。

    纪律：槽回调异常只记日志、绝不外抛（对齐 ``on_update``——诊断通道
    永远不能炸掉业务操作）。
    """

    def __init__(
        self, on_diagnostic: Optional[Callable[[NovaDiagnostic], None]] = None
    ) -> None:
        self.items: List[NovaDiagnostic] = []
        self._on_diagnostic = on_diagnostic

    def append(self, diagnostic: NovaDiagnostic) -> None:
        """收集一条诊断；槽位在场则同步外发。"""
        self.items.append(diagnostic)
        if self._on_diagnostic is not None:
            try:
                self._on_diagnostic(diagnostic)
            except Exception:
                logger.warning("Live diagnostic callback failed", exc_info=True)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> NovaDiagnostic:
        return self.items[index]

    def __iter__(self) -> Iterator[NovaDiagnostic]:
        return iter(self.items)


__all__ = ["DiagnosticsCollector"]
