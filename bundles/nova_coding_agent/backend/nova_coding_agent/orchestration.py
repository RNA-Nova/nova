"""Orchestrator 与裁决引擎的装配注册（bundle 进程级单例——对位 codex 的
handler 内 new 语义）。

分层：装配（政策引擎/审批流/遥测的组装）归 adjudication 扩展；本模块只做
"装配件放哪"的注册表。bash 等执行类工具执行期内 `new_orchestrator()` 现造
实例（对位 codex 每次调用 `ToolOrchestrator::new()`），裁决自报经
`get_adjudication_engine()` 取引擎。

未注册时（adjudication 扩展未激活）两者皆 None——工具按直通执行（组合声明
没装裁决扩展 = 裸奔，事实标配兜住）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from nova_harness.core.agent_session.controllers.orchestrator import Orchestrator

from nova_coding_agent.adjudication.engine import AdjudicationEngine


@dataclass(frozen=True)
class AdjudicationAssembly:
    """裁决装配件（orchestrator 工厂 + 政策引擎）"""

    new_orchestrator: Callable[[], Orchestrator]
    engine: AdjudicationEngine


_assembly: Optional[AdjudicationAssembly] = None


def register_adjudication_assembly(assembly: AdjudicationAssembly) -> None:
    """注册裁决装配件（adjudication 扩展激活时调用）。"""
    global _assembly
    _assembly = assembly


def new_orchestrator() -> Optional[Orchestrator]:
    """现造一个 Orchestrator；未注册（无裁决扩展）→ None"""
    if _assembly is None:
        return None
    return _assembly.new_orchestrator()


def get_adjudication_engine() -> Optional[AdjudicationEngine]:
    """取政策引擎；未注册 → None"""
    if _assembly is None:
        return None
    return _assembly.engine
