"""探针模型选型（对位 pi ``selectOneCasePerProvider``）。

每 provider 选 1 个最便宜的当代模型（同价按 id 字典序保确定性）——
e2e 验证行为契约，不做全模型扫描（可用性巡检归
``scripts/check_live_providers.py``）。
"""

from dataclasses import dataclass
from typing import Callable, List, Optional

from nova_protocol import Model

from nova_ai.gateway.models import create_models_manager
from nova_ai.providers.all import builtin_providers

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


def probe_for(
    models: List[Model], require: Optional[Callable[[Model], bool]] = None
) -> Optional[Model]:
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
    """汇总所有有凭据 provider 的探针（采集期同步：注册表 + 凭据存在性）。"""
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
