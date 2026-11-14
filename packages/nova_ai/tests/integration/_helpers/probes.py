"""探针模型选型（对位 pi ``selectOneCasePerProvider``）。

每 provider 选 1 个最便宜的当代模型（同价按 id 字典序保确定性）——
e2e 验证行为契约，不做全模型扫描（可用性巡检归
``tests/integration/check_live.py``）。
"""

import os
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, TypeVar

from nova_ai.gateway.models import create_models_manager
from nova_ai.providers.all import builtin_providers
from nova_protocol import AnyModel, Model

from .auth import has_credential

E2E_PROVIDERS = (
    "anthropic",
    "kimi-coding",
    "volcengine",
    "moonshotai",
    "moonshotai-cn",
)


@dataclass(frozen=True)
class Probe:
    """一个 e2e 探针（provider + 选定模型）。"""

    provider_id: str
    model: Model

    @property
    def label(self) -> str:
        return f"{self.provider_id}/{self.model.id}"


_ProbeModel = TypeVar("_ProbeModel", bound=AnyModel)


def probe_for(
    models: Sequence[_ProbeModel],
    require: Optional[Callable[[_ProbeModel], bool]] = None,
) -> Optional[_ProbeModel]:
    """最便宜优先（pi 的成本探针序），同价按 id 字典序。

    ``require``：能力过滤（如思考/视觉）——特性文件在**有该能力的模型里**
    选探针，避免最便宜的探针恰好无该能力而丢了整条链路的覆盖。
    """
    candidates = [m for m in models if require is None or require(m)]
    if not candidates:
        return None
    return min(candidates, key=lambda m: (m.cost.input + m.cost.output, m.id))


def collect_probes(
    require: Optional[Callable[[Model], bool]] = None,
) -> List[Probe]:
    """汇总探针；只有显式 integration 任务才允许读取真实环境/HOME。

    默认套件在 deselect 前也会 import 本模块，因此必须在收集期硬闸住。
    """
    if os.environ.get("NOVA_RUN_INTEGRATION") != "1":
        return []
    manager = create_models_manager()
    for provider in builtin_providers():
        manager.set_provider(provider)
    probes: List[Probe] = []
    for provider_id in E2E_PROVIDERS:
        if not has_credential(provider_id):
            continue
        probe = probe_for(manager.get_models(provider_id), require)
        if probe is not None:
            probes.append(Probe(provider_id, probe))
    return probes
